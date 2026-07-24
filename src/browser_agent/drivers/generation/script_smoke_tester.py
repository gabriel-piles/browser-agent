"""Run the final emitted script as a subprocess to catch runtime errors.

The system prompt promises a self-test (Step 11): "The framework runs
the final script in a separate subprocess for a short window before
declaring success." This module delivers that promise.

The validation runner (``InProcessScriptRunnerAdapter``) tests the
LLM's *raw* code in-process with shimmed ``start_browser``. The final
emitted script has vendored helpers prepended and ``zd.start()``
rewritten — a *different* artifact. Running it once as a real
subprocess catches the class of bugs that only surface in the final
form: stripped imports that shadowed vendored helpers, JS string
concatenation errors, missing helper definitions, etc.

The smoke test is **best-effort**: a timeout is a PASS (the script is
running, doing real work — we don't want to wait for a full scrape).
Only an early crash (exit before the timeout with a non-zero code)
is a FAIL. The result is logged prominently and surfaced to the
operator so a broken script is never silently delivered.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Short window: we only want to catch *immediate* failures (syntax
# errors, import errors, crashes in the first navigation phase). A
# real scrape takes minutes; we don't wait for that. If the script is
# still running at the timeout, it passed the smoke test.
SMOKE_TEST_TIMEOUT_S = 60.0


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
    cmd = [sys.executable, str(script_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return SmokeTestResult(success=False, output=f"failed to launch: {exc}", timed_out=False)

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SMOKE_TEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        _ = await proc.wait()
        return SmokeTestResult(success=True, output="[smoke test timed out — script is running]", timed_out=True)

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    if proc.returncode == 0:
        return SmokeTestResult(success=True, output=output, timed_out=False)
    return SmokeTestResult(success=False, output=output, timed_out=False)


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
    logger.error("smoke test output:\n{output}", output=result.output)
