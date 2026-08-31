"""Execute one flow script as a subprocess with the SHARED run-root stores.

Mirrors the legacy :class:`SubtaskExecutor` subprocess shape (streamed
output, live log, process-group kill on timeout) with the flow's
storage contract: every script writes its downloads and metadata rows
to the RUN ROOT's ``downloads/`` and ``metadata.db`` — the only two
artifacts shared across splits. The executor guarantees that via env
vars regardless of how the script computes its paths:

- ``BROWSER_AGENT_SAVE_RECORD_DB_PATH`` → ``<run>/metadata.db`` so
  ``save_record``/``discovered_links_store`` never resolve a
  split-local DB from ``__file__``.
- ``cwd`` → the run root so a bare ``Path("downloads")`` resolves to
  the shared folder, and the shared-store lint gate pushes scripts to
  use the run-root-relative ``__file__`` form anyway.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import TextIO

from loguru import logger

from browser_agent.domain.script_execution_report import ScriptExecutionReport

_RUN_TIMEOUT_S = int(os.environ.get("SCRAPER_RUN_TIMEOUT_S", str(6 * 3600)))


class FlowScriptExecutor:
    """Run one flow-emitted .py script as a subprocess, return a report."""

    def __init__(self, run_path: Path, live_log_path: Path) -> None:
        self._run_path: Path = run_path
        self._live_log_path: Path = live_log_path

    async def run(self, subtask_id: str, script_path: Path) -> ScriptExecutionReport:
        """Execute ``script_path``; stream output to the live log; return the report."""
        from browser_agent.configuration import ZENDRIVER_HEADLESS

        headless = "true" if ZENDRIVER_HEADLESS else "false"
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "ZENDRIVER_HEADLESS": headless,
            "BROWSER_AGENT_TASK_SLUG": subtask_id,
            "BROWSER_AGENT_SAVE_RECORD_DB_PATH": str(self._run_path / "metadata.db"),
        }
        cmd = [sys.executable, str(script_path)]
        t0 = time.monotonic()
        self._live_log_path.parent.mkdir(parents=True, exist_ok=True)
        live_fh = self._live_log_path.open("a", encoding="utf-8")
        live_fh.write(f"=== execution start {time.strftime('%Y-%m-%dT%H:%M:%S')} script={script_path} ===\n")
        live_fh.flush()
        logger.info("flow subtask {id}: executing script={p}", id=subtask_id, p=script_path)
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
                cwd=str(self._run_path),
            )
        except OSError as exc:
            logger.error("failed to launch flow subtask subprocess: {exc}", exc=exc)
            live_fh.close()
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
                self._stream_output(proc, output_lines, live_fh),
                timeout=_RUN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            await self._kill_process_group(proc)
            timed_out = True
            exit_code = 124
            logger.error("flow subtask timed out after {t}s - killed process group", t=_RUN_TIMEOUT_S)

        if not timed_out:
            exit_code = proc.returncode if proc.returncode is not None else 1

        duration_s = time.monotonic() - t0
        output_tail = "".join(output_lines[-100:])
        live_fh.write(f"=== execution end exit={exit_code} timed_out={timed_out} duration={duration_s:.1f} ===\n")
        live_fh.flush()
        live_fh.close()

        return ScriptExecutionReport(
            subtask_id=subtask_id,
            script_path=str(script_path),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_s=duration_s,
            output_tail=output_tail,
        )

    async def _stream_output(
        self,
        proc: asyncio.subprocess.Process,
        lines: list[str],
        live_fh: TextIO,
    ) -> list[str]:
        """Stream the subprocess's combined output; return the collected lines."""
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
            live_fh.write(text)
            live_fh.flush()
            lines.append(text)
        await proc.wait()
        return lines

    @staticmethod
    async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
        """Kill the subprocess's whole process group (Python + Chromium children)."""
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
