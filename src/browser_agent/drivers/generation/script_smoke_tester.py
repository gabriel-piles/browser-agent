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
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Short window: we only want to catch *immediate* failures (syntax
# errors, import errors, crashes in the first navigation phase). A
# real scrape takes minutes; we don't wait for that. If the script is
# still running at the timeout, it passed the smoke test.
SMOKE_TEST_TIMEOUT_S = 60.0

_ZD_RUNTIME_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        "tab.evaluate",
        "evaluate() missing 1 required positional argument",
        "tab.evaluate — called without expression argument",
    ),
    (
        "TypeError: object NoneType can't be used in 'await' expression",
        "save_record sync",
        "save_record — awaited synchronous helper",
    ),
    (
        "AttributeError: module 'zendriver' has no attribute 'start'",
        "zd.start not found",
        "zendriver.start — no such function",
    ),
    ("TypeError: 'NoneType' object is not callable", "NoneType called", "zendriver object was None — wrong browser startup"),
    (
        "TimeoutError: wait_for_anchors timed out after",
        "wait_for_anchors timeout",
        "wait_for_anchors — zero matches or wrong selector",
    ),
    (
        "ModuleNotFoundError: No module named 'playwright'",
        "playwright import",
        "agent imports playwright instead of zendriver CDP",
    ),
    ("KeyError: 'file_size'", "file_size key", "result dict has no file_size key; use 'size'"),
    (
        "zendriver.core.connection.ProtocolException",
        "ProtocolException",
        "zendriver CDP protocol error — bad evaluate() call",
    ),
    ("zendriver.core.elements.ElementNotFound", "ElementNotFound", "zendriver element not found — wrong selector"),
    # Generic Python errors indicating the agent's script is structurally broken
    ("NameError: name '", "NameError", "undefined variable — agent used wrong API name"),
    ("SyntaxError: invalid syntax", "SyntaxError", "syntax error — agent emitted malformed Python"),
    ("ImportError: cannot import name '", "ImportError", "import error — agent imports wrong module/name"),
    ("ModuleNotFoundError: No module named '", "ModuleNotFoundError", "missing module — agent imports non-existent package"),
    ("AttributeError: '", "AttributeError", "attribute error — agent called wrong method/property"),
    ("TypeError: ", "TypeError", "type error — agent passed wrong argument type"),
    (
        "asyncio.run() cannot be called from a running event loop",
        "asyncio.run error",
        "agent used asyncio.run() inside running loop",
    ),
]


@dataclass
class SmokeTestResult:
    """Outcome of running the emitted script as a subprocess."""

    success: bool
    output: str
    timed_out: bool


async def smoke_test_script(script_path: Path) -> SmokeTestResult:
    """Run ``script_path`` as a subprocess with a short timeout.

    Returns a :class:`SmokeTestResult`. A timeout is treated as
    success (the script is running without crashing). A non-zero
    exit before the timeout is a failure — the output carries the
    traceback so the operator can see the root cause immediately.
    """
    scratch_dir = _scratch_dir(script_path)
    db_path = str(scratch_dir / "metadata.db")
    cmd = [sys.executable, str(script_path)]
    env = {
        **os.environ,
        "ZENDRIVER_HEADLESS": "true",
        "BROWSER_AGENT_SAVE_RECORD_DB_PATH": db_path,
        "BROWSER_AGENT_TASK_SLUG": "smoke",
    }
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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SMOKE_TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _kill_process_group(proc)
        return SmokeTestResult(success=True, output="[smoke test timed out — script is running]", timed_out=True)

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    if proc.returncode == 0:
        return SmokeTestResult(success=True, output=output, timed_out=False)
    return SmokeTestResult(success=False, output=output, timed_out=False)


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


def _log_smoke_failure(output: str) -> None:
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
    logger.warning("smoke test FAILED{summary}", summary=summary)


def _log_zendriver_errors_in_output(output: str) -> None:
    """Scan ``output`` for patterns indicating zendriver API misuse and log them."""
    found: list[str] = []
    for pattern, label, description in _ZD_RUNTIME_ERROR_PATTERNS:
        if pattern in output:
            found.append(description)
            logger.warning(
                "[SMOKE ZD-ERROR] — {label}: {description}",
                label=label,
                description=description,
            )
    if found:
        logger.warning(
            "smoke test — zendriver runtime errors: {count} issue(s) — {gaps}",
            count=len(found),
            gaps="; ".join(found),
        )


def log_smoke_test_result(result: SmokeTestResult, script_path: Path) -> None:
    """Log the smoke-test result prominently so the operator sees it."""
    if result.success:
        if result.timed_out:
            logger.info(
                "smoke test PASSED (timed out after {t}s — script running without crash): {path}",
                t=int(SMOKE_TEST_TIMEOUT_S),
                path=script_path,
            )
        else:
            logger.info("smoke test PASSED: {path}", path=script_path)
        return
    logger.error("smoke test FAILED: {path}", path=script_path)
    _log_smoke_failure(result.output)
    _log_zendriver_errors_in_output(result.output)
    logger.error("smoke test output:\n{output}", output=result.output)
