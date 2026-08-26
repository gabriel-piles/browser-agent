"""Run the final emitted script as a subprocess to catch runtime errors.

The system prompt promises a self-test (Step 11): "The framework runs
the final script in a separate subprocess for a short window before
declaring success." This module delivers that promise.

The validation runner (``InProcessScriptRunnerAdapter``) tests the
LLM's *raw* code in-process with shimmed ``start_browser``. The final
emitted script imports from ``script_tools/`` and has ``zd.start()``
rewritten — a *different* artifact. Running it once as a real
subprocess catches the class of bugs that only surface in the final
form: missing ``script_tools/`` copy, JS string concatenation errors,
import shadowing, etc.

The smoke test is **best-effort**: a timeout is a PASS (the script is
running, doing real work — we don't want to wait for a full scrape).
Only an early crash (exit before the timeout with a non-zero code)
is a FAIL. The result is logged prominently and surfaced to the
operator so a broken script is never silently delivered.

SCRATCH ISOLATION: the script is launched with
``BROWSER_AGENT_SAVE_RECORD_DB_PATH`` and
``BROWSER_AGENT_TASK_SLUG`` env vars set so the smoke test writes
rows into a scratch ``metadata.db`` and PDFs into a scratch
``downloads/`` dir under the run's ``smoke/`` folder — NOT the real
run directory. This prevents the 60-second window from dirtying the
run with partial rows and locked PDFs that step 1 then has to
untangle.

PROCESS GROUP: the subprocess is launched with
``start_new_session=True`` so the Python process and the Chromium it
spawns share a process group. On timeout the whole group is killed
(SIGTERM then SIGKILL), preventing orphaned browsers with locked
profiles.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import sys

from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.clean_browser_launcher import kill_chromium_under

from browser_agent.domain.processing_self_check_result import ProcessingSelfCheckResult
from browser_agent.domain.smoke_test_result import SmokeTestResult

from browser_agent.script_tools.discovered_links_store import preseed_sample_links

from browser_agent.use_cases.pdf_url_matcher import PdfUrlMatcher
from browser_agent.use_cases.zendriver_error_patterns import ZD_RUNTIME_ERROR_PATTERNS

# Short window: we only want to catch *immediate* failures (syntax
# errors, import errors, crashes in the first navigation phase). A
# real scrape takes minutes; we don't wait for that. If the script is
# still running at the timeout, it passed the smoke test.
SMOKE_TEST_TIMEOUT_S = 60.0


def _use_smoke_profile(script_path: Path, scratch_dir: Path) -> tuple[Path | None, str | None]:
    """Temporarily override ``run_config.py`` to use a scratch profile.

    The generation pipeline's Chromium may still hold a lock on the
    run's real profile directory. The smoke-test subprocess cannot
    launch another Chromium instance against the same profile, so we
    redirect ``PROFILE_PATH`` to an empty scratch dir.

    Returns ``(run_config_path, original_content)`` — or ``(None, None)``
    if ``run_config.py`` does not exist (defensive).
    """
    run_config_path = script_path.parent / "script_tools" / "run_config.py"
    if not run_config_path.exists():
        return None, None

    original = run_config_path.read_text(encoding="utf-8")
    smoke_profile = scratch_dir / "profile"
    smoke_profile.mkdir(parents=True, exist_ok=True)
    temp_config = f"PROFILE_PATH = {str(smoke_profile.resolve())!r}\nNOPECHA_EXTENSION_DIR = None\n"
    run_config_path.write_text(temp_config, encoding="utf-8")
    return run_config_path, original


def _restore_run_config(path: Path | None, content: str | None) -> None:
    """Restore original ``run_config.py`` content (no-op if nothing was swapped)."""
    if path is not None and content is not None:
        path.write_text(content, encoding="utf-8")


def _reap_scratch_chromium(profile_dir: Path) -> None:
    """Best-effort: terminate Chromium whose ``--user-data-dir`` is under ``profile_dir``.

    The smoke-test subprocess starts Chromium in its own process group
    (``start_new_session=True`` in the script's ``start_browser``), so killing
    the script's group leaves the browser alive on any exit path. Reap it so
    no visible window survives the smoke test or self-check.
    """
    try:
        kill_chromium_under(profile_dir)
    except Exception as exc:
        logger.warning("Chromium reap failed under {dir}: {exc}", dir=profile_dir, exc=exc)


async def smoke_test_script(
    script_path: Path,
    timeout: float = SMOKE_TEST_TIMEOUT_S,
    timeout_is_success: bool = True,
) -> SmokeTestResult:
    """Run ``script_path`` as a subprocess with ``timeout`` seconds.

    Returns a :class:`SmokeTestResult`. By default a timeout is treated
    as success (the script is running without crashing) — pass
    ``timeout_is_success=False`` for scripts that MUST finish and print
    a verdict (e.g. discovery self-check) so a hang is a real failure.
    A non-zero exit before the timeout is always a failure.
    """
    scratch_dir = _scratch_dir(script_path)
    db_path = str(scratch_dir / "metadata.db")

    # Use a scratch profile so the smoke test does not collide with
    # the generation pipeline's still-running Chromium instance.
    run_config_path, original_run_config = _use_smoke_profile(script_path, scratch_dir)

    cmd = [sys.executable, str(script_path)]
    env = {
        **os.environ,
        "ZENDRIVER_HEADLESS": "true",
        "BROWSER_AGENT_SAVE_RECORD_DB_PATH": db_path,
        "BROWSER_AGENT_TASK_SLUG": "smoke",
    }
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            return SmokeTestResult(success=False, output=f"failed to launch: {exc}", timed_out=False)

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill_process_group(proc)
            return SmokeTestResult(
                success=timeout_is_success,
                output="[smoke test timed out — script is running]"
                if timeout_is_success
                else f"[timed out after {timeout}s — script hung]",
                timed_out=True,
            )

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        if proc.returncode == 0:
            return SmokeTestResult(success=True, output=output, timed_out=False)
        return SmokeTestResult(success=False, output=output, timed_out=False)
    finally:
        _restore_run_config(run_config_path, original_run_config)
        _reap_scratch_chromium(scratch_dir / "profile")


async def processing_self_check(
    script_path: Path,
    sample_urls: list[str],
    timeout: float = 300.0,
) -> ProcessingSelfCheckResult:
    """Run the processing script against sample links; prove it downloads + saves.

    Seeds a scratch ``metadata.db`` with ``sample_urls`` as
    ``status='discovered'`` links, runs the script as a subprocess, then
    counts rows in the ``metadata`` table whose ``data`` JSON has
    ``download_status == "downloaded"`` and a non-empty ``pdf_filename``.
    A hang is a failure (``timeout_is_success=False``). Downloads land in
    ``<run>/downloads`` (the script computes ``out_dir`` from ``__file__``);
    this is benign — the real run's helpers treat already-present canonical
    files as "already downloaded" and skip.
    """
    scratch = script_path.parent.parent / "selfcheck"
    scratch.mkdir(parents=True, exist_ok=True)
    db_path = scratch / "metadata.db"
    if db_path.exists():
        db_path.unlink()
    preseed_sample_links(sample_urls, str(db_path), status="discovered", filter_label="selfcheck")
    run_config_path, original_run_config = _use_smoke_profile(script_path, scratch)
    cmd = [sys.executable, str(script_path)]
    env = {
        **os.environ,
        "BROWSER_AGENT_SAVE_RECORD_DB_PATH": str(db_path),
        "BROWSER_AGENT_TASK_SLUG": "selfcheck",
    }
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            return ProcessingSelfCheckResult(
                success=False, downloaded_rows=0, record_count=0, output=f"failed to launch: {exc}"
            )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill_process_group(proc)
            downloaded_rows, record_count, violations, has_download_intent = _analyze_records(db_path)
            ok = _self_check_ok(downloaded_rows, record_count, violations, has_download_intent)
            return ProcessingSelfCheckResult(
                success=ok,
                downloaded_rows=downloaded_rows,
                record_count=record_count,
                violations=violations,
                output=f"[timed out after {timeout}s — script hung]",
            )
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
    finally:
        _restore_run_config(run_config_path, original_run_config)
        _reap_scratch_chromium(scratch / "profile")
    downloaded_rows, record_count, violations, has_download_intent = _analyze_records(db_path)
    ok = _self_check_ok(downloaded_rows, record_count, violations, has_download_intent)
    return ProcessingSelfCheckResult(
        success=ok,
        downloaded_rows=downloaded_rows,
        record_count=record_count,
        violations=violations,
        output=output,
    )


def _analyze_records(db_path: Path) -> tuple[int, int, list[str], bool]:
    """Return (downloaded_rows, total_rows, violations, has_download_intent) from the scratch metadata table.

    Violations are deterministic correctness bugs, one human-readable line each:
      - canonical_filename: a downloaded row's core_pdf_filename stem (the part
        before the last '.') is not the pdf_<hash>/doc_<hash> stem derived from
        core_file_url (content-typed names like doc_<hash>.pdf pass; a mismatched
        hash fails)
      - failed_download: a row with core_file_url but core_download_status != "downloaded"
        or empty core_pdf_filename
      - load_failed: a row with core_download_status == "load_failed" (metadata gate never rendered)
    ``has_download_intent`` is true when any row carries a ``core_file_url``,
    ``core_pdf_filename``, or a non-trivial ``core_download_status`` —
    """
    if not db_path.exists():
        return 0, 0, [], False
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        rows = conn.execute("SELECT data FROM metadata").fetchall()
    except sqlite3.OperationalError:
        return 0, 0, [], False
    finally:
        conn.close()
    downloaded = 0
    has_download_intent = False
    violations: list[str] = []
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        fu = data.get("core_file_url")
        pf = data.get("core_pdf_filename")
        status = data.get("core_download_status")
        if fu or pf or (status and status != "no_files"):
            has_download_intent = True
        if status == "downloaded" and pf:
            downloaded += 1
            if fu and pf:
                stem = pf.rsplit(".", 1)[0]
                doc_stem, pdf_stem = PdfUrlMatcher.stems_for(fu)
                if stem not in (doc_stem, pdf_stem):
                    violations.append(f"canonical_filename: {pf!r} != {doc_stem}/{pdf_stem} stem (core_file_url={fu})")
        elif status == "load_failed":
            violations.append(f"load_failed: core_source_page_url={data.get('core_source_page_url')!r}")
        elif fu:
            violations.append(f"failed_download: status={status!r} core_pdf_filename={pf!r} (core_file_url={fu!r})")
    if len(rows) == 0:
        violations.append("zero_records: script ran but saved no metadata rows")
    return downloaded, len(rows), violations, has_download_intent


def _self_check_ok(downloaded_rows: int, record_count: int, violations: list[str], has_download_intent: bool) -> bool:
    """True when the self-check passes, tolerating extract-only (no-download) tasks.

    Download tasks (any row carries a download field) require at least one
    downloaded row; extract-only tasks pass on ``record_count >= 1``.
    Violations always fail.
    """
    if violations:
        return False
    if has_download_intent:
        return downloaded_rows >= 1
    return record_count >= 1


def _scratch_dir(script_path: Path) -> Path:
    """Return a scratch dir for smoke-test DB + downloads under the run."""
    run_path = script_path.parent.parent
    scratch = run_path / "smoke"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the process group (Python + Chromium child) on timeout."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def _log_smoke_failure(output: str, attempt: int = 1) -> None:
    """Catch-all: log the root error line from any smoke test failure."""
    lines = output.strip().split("\n")
    error_line = ""
    for line in reversed(lines):
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("Traceback")
            and not stripped.startswith("File ")
            and not stripped.startswith("  ")
        ):
            error_line = stripped
            break
    summary = f" — root error: {error_line}" if error_line else ""
    logger.warning("smoke test FAILED (attempt {n}){summary}", n=attempt, summary=summary)


def _log_zendriver_errors_in_output(output: str, attempt: int = 1) -> None:
    """Scan ``output`` for patterns indicating zendriver API misuse and log them."""
    found: list[str] = []
    for pattern, label, description in ZD_RUNTIME_ERROR_PATTERNS:
        if pattern in output:
            found.append(description)
            logger.warning(
                "[SMOKE ZD-ERROR] — {label}: {description}",
                label=label,
                description=description,
            )
    if found:
        logger.warning(
            "smoke test (attempt {n}) — zendriver runtime errors: {count} issue(s) — {gaps}",
            n=attempt,
            count=len(found),
            gaps="; ".join(found),
        )


def log_smoke_test_result(result: SmokeTestResult, script_path: Path, attempt: int = 1) -> None:
    """Log the smoke-test result prominently so the operator sees it."""
    if result.success:
        if result.timed_out:
            logger.info(
                "smoke test PASSED (attempt {n}) (timed out after {t}s — script running without crash): {path}",
                n=attempt,
                t=int(SMOKE_TEST_TIMEOUT_S),
                path=script_path,
            )
        else:
            logger.info("smoke test PASSED (attempt {n}): {path}", n=attempt, path=script_path)
        return
    logger.error("smoke test FAILED (attempt {n}): {path}", n=attempt, path=script_path)
    _log_smoke_failure(result.output, attempt=attempt)
    _log_zendriver_errors_in_output(result.output, attempt=attempt)
    logger.error("smoke test output:\n{output}", output=result.output)
