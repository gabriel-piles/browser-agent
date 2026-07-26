"""Run read-only Python scripts in an isolated subprocess.

Powers the verification agent's ``run_read_script`` tool. The agent
writes forensic scripts that cross-reference ``metadata.db`` against
``downloads/`` and inspect file integrity. This adapter writes the code
to a temp file and runs it with the project's Python (``sys.executable``)
so ``sqlite3`` / ``pathlib`` / ``pypdf`` (if installed) are available.

A timeout is a FAILURE — a forensic script that hangs is a bug, not a
pass (unlike :func:`smoke_test_script`, where a timeout means the script
is doing real work). Output is truncated tail-biased to keep the agent's
context window safe.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.read_script_runner_port import ReadScriptRunnerPort

_OUTPUT_MAX = 8000


class SubprocessReadScriptRunner(ReadScriptRunnerPort):
    """Stateless subprocess runner for read-only forensic scripts."""

    async def run(self, python_code: str, timeout: float = 60.0) -> ScriptExecutionResult:
        """Execute ``python_code`` and return the captured result."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(python_code)
            tmp_path = Path(tmp.name)
        try:
            return await self._run_subprocess(tmp_path, timeout)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def _run_subprocess(self, script_path: Path, timeout: float) -> ScriptExecutionResult:
        """Run the script file with the project Python, enforcing ``timeout``."""
        cmd = [sys.executable, str(script_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return ScriptExecutionResult(exit_code=1, output=f"failed to launch: {exc}", success=False)

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            _ = await proc.wait()
            return ScriptExecutionResult(
                exit_code=124,
                output=f"[script timed out after {timeout}s — treated as failure]",
                success=False,
            )

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        output = _truncate_tail(output, _OUTPUT_MAX)
        success = proc.returncode == 0
        return ScriptExecutionResult(
            exit_code=proc.returncode if proc.returncode is not None else 1,
            output=output,
            success=success,
        )


def _truncate_tail(output: str, limit: int) -> str:
    """Return ``output`` truncated tail-biased to ``limit`` chars."""
    if len(output) <= limit:
        return output
    keep = limit - 60
    return f"...(head trimmed, total={len(output)} chars)\n{output[-keep:]}"
