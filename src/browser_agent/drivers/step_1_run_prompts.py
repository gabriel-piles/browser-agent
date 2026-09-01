"""Step 1 of the split-run flow: run the selected split prompts sequentially.

Usage:
    python -m browser_agent.drivers.step_1_run_prompts

Reads ``active_run.yaml`` (active run + the new ``active_flow`` selector:
numbers or ranges like ``2-5,6,125-455`` naming step-0 split prefixes)
and runs each selected split folder's prompt end-to-end: an exploration
agent verifies the split's pages and emits its spec, a writer agent
builds the script (seeded with the PREVIOUS split's script when its
mechanics transfer), the deterministic pipeline lints, smokes,
executes, and an independent verify agent judges coverage and decides
whether to rewrite the script, request an extra script, or accept the
gap. Splits run strictly in order — the next never starts before the
previous finished. Each split folder is self-contained (scripts,
verification, logs, profile, debug); only ``downloads/`` and
``metadata.db`` are shared at the run root.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

from loguru import logger

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.agent_logging import log_llm_total_summary, reset_llm_estimates
from browser_agent.drivers.flow.active_flow_parser import ActiveFlowError, parse_active_flow
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.drivers.run_elapsed_heartbeat import RunElapsedHeartbeat
from browser_agent.drivers.signal_guard import SignalGuard
from browser_agent.drivers.stall_watchdog import StallWatchdog
from browser_agent.llm_transcript_logger import configure_llm_transcript_dir
from browser_agent.logging_config import add_run_log_file, configure_logging
from browser_agent.use_cases.debug_bundle_writer import DebugBundleWriter

DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."


class RunPromptsDriver:
    """Step-1 driver: selected splits → explorer → writer → pipeline → verifier."""

    def __init__(self) -> None:
        self._task_reader: TaskReader = TaskReader(DEFAULT_PROMPT)

    def run(self, argv: list[str]) -> int:
        """Configure logging, run the async flow, return the process exit code."""
        configure_logging()
        reset_llm_estimates()
        try:
            return asyncio.run(self._run_async(argv))
        finally:
            log_llm_total_summary()

    async def _run_async(self, argv: list[str]) -> int:
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        logs_dir = run_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        add_run_log_file(logs_dir / "run.log")
        logger.info("run log file enabled path={p}", p=logs_dir / "run.log")
        debug_dir = run_path / "debug"
        configure_llm_transcript_dir(debug_dir / "llm")
        logger.info("debug bundle enabled dir={p}", p=debug_dir)
        watchdog = StallWatchdog()
        watchdog.attach(logs_dir / "stall_dump.log")
        watchdog.arm()
        heartbeat = RunElapsedHeartbeat(watchdog)
        heartbeat.start()
        guard = SignalGuard()
        guard.install()
        outcome = "finished"
        error_text = ""
        try:
            code = await self._run_flow(run, run_path, argv)
            if code != 0:
                outcome = "failed"
            return code
        except asyncio.CancelledError:
            outcome = f"interrupted_by_{guard.signal_name().lower()}"
            logger.warning("run cancelled by signal={sig} — rerun the same command to resume", sig=guard.signal_name())
            return guard.exit_code()
        except Exception:
            outcome = "crashed"
            error_text = traceback.format_exc()
            logger.exception("run-prompts driver failed")
            return 2
        finally:
            await self._cleanup(guard, heartbeat, watchdog, run_path)
            _safe(_write_debug_bundle, run, run_path, outcome, error_text)

    async def _run_flow(self, run, run_path, argv: list[str]) -> int:
        """Load the flow selection, resolve split dirs, run the orchestrator."""
        from browser_agent.adapters.browser.clean_browser_launcher import kill_chromium_under
        from browser_agent.drivers.flow.flow_orchestrator import FlowOrchestrator
        from browser_agent.drivers.flow.split_selector import resolve_split_dirs
        from browser_agent.use_cases.metadata_db import ensure_metadata_schema

        kill_chromium_under(run_path)
        ensure_metadata_schema(run_path / "metadata.db")
        selection = parse_active_flow(self._active_flow_raw())
        task = self._read_task(argv, run)
        split_dirs = resolve_split_dirs(run_path, selection)
        if not split_dirs:
            logger.error("active_flow selected no existing split folders (orders={orders})", orders=selection.describe())
            return 1
        logger.info(
            "run-prompts driver starting run={run} flow={flow} splits={splits} task_tokens={n}",
            run=run.name,
            flow=selection.describe(),
            splits=[d.name for d in split_dirs],
            n=len(task) // 4,
        )
        require_html_files = bool(run.scraper_registry_template)
        orchestrator = FlowOrchestrator(
            run_path=run_path,
            require_html_files=require_html_files,
            original_task=task,
        )
        return await orchestrator.run(split_dirs)

    def _active_flow_raw(self) -> str:
        """Return the raw ``active_flow`` value from ``active_run.yaml``."""
        import yaml

        from browser_agent.configuration import RUNS_FILE

        data = yaml.safe_load(RUNS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ActiveFlowError(f"active_run.yaml must be a mapping (got {data!r})")
        return str(data.get("active_flow", ""))

    def _read_task(self, argv: list[str], run) -> str:
        return self._task_reader.read(argv, run)

    async def _cleanup(self, guard, heartbeat, watchdog, run_path) -> None:
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir, kill_chromium_under

        _safe(guard.uninstall)
        try:
            await heartbeat.stop()
        except Exception:
            logger.exception("heartbeat stop failed")
        _safe(watchdog.close)
        _safe(kill_chromium_under, run_path)
        _safe(delete_profile_dir, run_path / "profile")
        _safe(delete_profile_dir, run_path / "profile_builder")
        _safe(delete_profile_dir, run_path / "profile_verifier")
        for split in self._split_profiles(run_path):
            _safe(delete_profile_dir, split)
        for scratch in self._scratch_profiles(run_path):
            _safe(delete_profile_dir, scratch)

    @staticmethod
    def _split_profiles(run_path) -> list[Path]:
        """Every per-split Chromium profile directory under ``flow/*/profile``."""
        flow = run_path / "flow"
        if not flow.is_dir():
            return []
        return [entry / "profile" for entry in sorted(flow.iterdir()) if entry.is_dir() and (entry / "profile").is_dir()]

    @staticmethod
    def _scratch_profiles(run_path) -> list[Path]:
        """Per-split smoke/self-check scratch profiles under ``flow/*/*/profile``.

        ``smoke_test_script`` / ``processing_self_check`` point the smoke runs
        at scratch dirs derived from each emitted script's path, so their
        Chromium profiles land at ``flow/<split>/smoke/profile`` and
        ``flow/<split>/selfcheck/profile`` — outside the split's own
        ``profile/`` tree and thus missed by :meth:`_split_profiles`.
        """
        flow = run_path / "flow"
        if not flow.is_dir():
            return []
        return [
            area / "profile"
            for entry in sorted(flow.iterdir())
            if entry.is_dir()
            for area in (entry / "smoke", entry / "selfcheck")
            if area.is_dir() and (area / "profile").is_dir()
        ]


def _safe(fn, *args) -> None:
    """Run ``fn(*args)``, logging (not raising) any failure so cleanup continues."""
    try:
        fn(*args)
    except Exception:
        logger.exception("cleanup step failed: {fn}", fn=getattr(fn, "__name__", repr(fn)))


def _write_debug_bundle(run, run_path, outcome: str, error_text: str) -> None:
    DebugBundleWriter(run_path).write(run, outcome, error_text)


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(RunPromptsDriver().run(sys.argv))


if __name__ == "__main__":
    main()
