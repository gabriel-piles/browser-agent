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

from browser_agent.domain.processing_self_check_result import ProcessingSelfCheckResult
from browser_agent.domain.smoke_test_result import SmokeTestResult

from browser_agent.script_tools.discovered_links_store import preseed_sample_links
from browser_agent.script_tools._file_utils import pdf_id_for

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
            downloaded_rows, record_count, violations = _analyze_records(db_path)
            return ProcessingSelfCheckResult(
                success=downloaded_rows >= 1 and not violations,
                downloaded_rows=downloaded_rows,
                record_count=record_count,
                violations=violations,
                output=f"[timed out after {timeout}s — script hung]",
            )
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
    finally:
        _restore_run_config(run_config_path, original_run_config)
    downloaded_rows, record_count, violations = _analyze_records(db_path)
    return ProcessingSelfCheckResult(
        success=downloaded_rows >= 1 and not violations,
        downloaded_rows=downloaded_rows,
        record_count=record_count,
        violations=violations,
        output=output,
    )


def _analyze_records(db_path: Path) -> tuple[int, int, list[str]]:
    """Return (downloaded_rows, total_rows, violations) from the scratch metadata table.

    Violations are deterministic correctness bugs, one human-readable line each:
      - canonical_filename: a downloaded row's pdf_filename != pdf_id_for(file_url)+".pdf"
      - failed_download: a row with file_url but download_status != "downloaded" or empty pdf_filename
      - load_failed: a row with download_status == "load_failed" (metadata gate never rendered)
    """
    if not db_path.exists():
        return 0, 0, []
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        rows = conn.execute("SELECT data FROM metadata").fetchall()
    except sqlite3.OperationalError:
        return 0, 0, []
    finally:
        conn.close()
    downloaded = 0
    violations: list[str] = []
    for (raw,) in rows:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        fu = data.get("file_url")
        pf = data.get("pdf_filename")
        sf = data.get("supporting_filename")
        status = data.get("download_status")
        if status == "downloaded" and (pf or sf):
            downloaded += 1
            if fu and pf and pf != pdf_id_for(fu) + ".pdf":
                violations.append(f"canonical_filename: {pf!r} != {pdf_id_for(fu) + '.pdf'!r} (file_url={fu})")
        elif status == "load_failed":
            violations.append(f"load_failed: source_page_url={data.get('source_page_url')!r}")
        elif fu:
            violations.append(f"failed_download: status={status!r} pdf_filename={pf!r} (file_url={fu})")
    return downloaded, len(rows), violations


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
