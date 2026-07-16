"""Run the emitted script standalone for a short window as a smoke test.

The in-process validation runner shares the agent's browser and
shims ``import zendriver`` / ``start_browser()``, so it can hide
integration mistakes in the final file. This gate starts the
script as the operator will run it (``python <file>`` in the
project's virtualenv) and lets it execute for up to
``SMOKE_TIMEOUT_S`` seconds. If the script exits non-zero or the
timeout fires, we log the captured output and raise so the
caller knows the deliverable is broken before the operator
discovers it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

# Hard cap on how long the smoke test lets the emitted script run.
SMOKE_TIMEOUT_S = 20.0


class EmittedScriptSmokeRunner:
    """Run the emitted script in a subprocess and surface any failure."""

    async def run(self, script_path: Path) -> None:
        """Spawn ``python <script_path>`` and raise when it fails the smoke test."""
        venv_python = Path(sys.executable)
        self._log_start(script_path, venv_python)
        proc = await self._spawn(script_path, venv_python)
        await self._await_exit(proc, script_path)

    def _log_start(self, script_path: Path, venv_python: Path) -> None:
        """Log the smoke-run banner with timeout + interpreter path."""
        logger.info(
            "smoke-running emitted script for up to {timeout}s: {path} (python={py})",
            timeout=SMOKE_TIMEOUT_S,
            path=script_path,
            py=venv_python,
        )

    async def _spawn(self, script_path: Path, venv_python: Path):
        """Spawn the smoke subprocess inheriting the current environment."""
        return await asyncio.create_subprocess_exec(
            str(venv_python),
            str(script_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(script_path.parent),
            env=os.environ.copy(),
        )

    async def _await_exit(self, proc, script_path: Path) -> None:
        """Wait for the subprocess; raise on non-zero, log on timeout."""
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SMOKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            await self._on_timeout(proc)
            return
        self._raise_on_failure(proc, stdout, script_path)

    async def _on_timeout(self, proc) -> None:
        """Kill the subprocess and log the timeout warning."""
        proc.kill()
        await proc.communicate()
        logger.warning(
            "smoke-run timed out after {timeout}s (script still running)",
            timeout=SMOKE_TIMEOUT_S,
        )

    def _raise_on_failure(self, proc, stdout, script_path: Path) -> None:
        """Raise :class:`RuntimeError` when the subprocess exited non-zero."""
        if proc.returncode == 0:
            logger.info("smoke-run succeeded for {path}", path=script_path)
            return
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        logger.error(
            "smoke-run failed for {path}\n{output}",
            path=script_path,
            output=output,
        )
        raise RuntimeError(
            f"Emitted script failed during smoke run " f"(exit_code={proc.returncode}): {script_path}\n{output}"
        )
