from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from loguru import logger

from browser_agent.configuration import PROJECT_ROOT
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.script_runner_port import ScriptRunnerPort


class SubprocessScriptRunnerAdapter(ScriptRunnerPort):
    """Runs generated scripts using the project's virtualenv Python.

    Writes the code to a temp file, executes it with the ``.venv``
    Python (so zendriver and all declared dependencies are available),
    captures combined stdout/stderr, and enforces a timeout. The temp
    file is always cleaned up — even on timeout or exception.
    """

    _VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
    _MAX_OUTPUT = 8000
    _DEFAULT_TIMEOUT = 120.0

    async def run(self, python_code: str, timeout: float = _DEFAULT_TIMEOUT) -> ScriptExecutionResult:
        tmp = self._write_temp(python_code)
        try:
            return await self._execute(tmp, timeout)
        finally:
            self._cleanup(tmp)

    def _write_temp(self, python_code: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".py", prefix="validation_")
        os.close(fd)
        Path(path).write_text(python_code, encoding="utf-8")
        return Path(path)

    async def _execute(self, script_path: Path, timeout: float) -> ScriptExecutionResult:
        interpreter = self._resolve_interpreter()
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ScriptExecutionResult(
                exit_code=124,
                output=f"[TIMEOUT after {timeout:.0f}s — script was killed]",
                success=False,
            )
        output = raw.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        return ScriptExecutionResult(
            exit_code=exit_code,
            output=output[: self._MAX_OUTPUT],
            success=exit_code == 0,
        )

    def _resolve_interpreter(self) -> str:
        """Return the venv Python, falling back to ``sys.executable``."""
        if self._VENV_PYTHON.exists():
            return str(self._VENV_PYTHON)
        logger.warning("venv python not found at {}, using sys.executable", self._VENV_PYTHON)
        return sys.executable

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.exception("failed to clean up temp script {}", path)