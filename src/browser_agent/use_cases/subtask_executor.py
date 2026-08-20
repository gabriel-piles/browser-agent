"""Execute one subtask's emitted script as a subprocess — ported from step_1."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from loguru import logger

from browser_agent.domain.script_execution_report import ScriptExecutionReport

_SUBTASK_RUN_TIMEOUT_S = int(os.environ.get("SCRAPER_RUN_TIMEOUT_S", str(6 * 3600)))


class SubtaskExecutor:
    """Run one emitted .py script as a subprocess, return a report."""

    async def run(self, subtask_id: str, script_path: Path) -> ScriptExecutionReport:
        import time

        headless = "true" if _headless_env() else "false"
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "ZENDRIVER_HEADLESS": headless,
            "BROWSER_AGENT_TASK_SLUG": subtask_id,
        }
        cmd = [sys.executable, str(script_path)]
        t0 = time.monotonic()
        output_lines: list[str] = []
        timed_out = False
        exit_code = 1

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            logger.error("failed to launch subtask subprocess: {exc}", exc=exc)
            return ScriptExecutionReport(
                subtask_id=subtask_id,
                script_path=str(script_path),
                exit_code=2,
                timed_out=False,
                duration_s=0.0,
                output_tail=str(exc),
            )

        try:
            output_lines = await asyncio.wait_for(
                self._stream_output(proc, output_lines),
                timeout=_SUBTASK_RUN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            await self._kill_process_group(proc)
            timed_out = True
            exit_code = 124
            logger.error(
                "subtask timed out after {t}s — killed process group",
                t=_SUBTASK_RUN_TIMEOUT_S,
            )

        if not timed_out:
            exit_code = proc.returncode if proc.returncode is not None else 1

        duration_s = time.monotonic() - t0
        output_tail = "".join(output_lines[-100:])

        return ScriptExecutionReport(
            subtask_id=subtask_id,
            script_path=str(script_path),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_s=duration_s,
            output_tail=output_tail,
        )

    async def _stream_output(self, proc: asyncio.subprocess.Process, lines: list[str]) -> list[str]:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
            lines.append(text)
        await proc.wait()
        return lines

    @staticmethod
    async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            _ = proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()


def _headless_env() -> bool:
    from browser_agent.configuration import ZENDRIVER_HEADLESS

    return ZENDRIVER_HEADLESS
