"""Step 0 of the split-run flow: discover the task and write split folders.

Usage:
    python -m browser_agent.drivers.step_0_discover_task

Drives a discover agent over the active run prompt, which splits the task
into WHAT-scoped chunks; one folder per chunk is written under
``data/runs/<run>/flow/`` as ``N_short_name`` containing ``prompt.md`` and
``split.json``. Re-runs are incremental: the agent gets the existing
splits as context and emits only NEW paths/families.
"""

from __future__ import annotations

import asyncio
import sys
import traceback

from loguru import logger

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.drivers.run_elapsed_heartbeat import RunElapsedHeartbeat
from browser_agent.drivers.signal_guard import SignalGuard
from browser_agent.drivers.stall_watchdog import StallWatchdog
from browser_agent.agent_logging import log_llm_total_summary, reset_llm_estimates
from browser_agent.domain.discover_plan import DiscoverPlan
from browser_agent.domain.run_config import RunConfig
from browser_agent.use_cases.discover_coverage_loop import DiscoverCoverageLoop
from browser_agent.llm_transcript_logger import configure_llm_transcript_dir
from browser_agent.logging_config import add_run_log_file, configure_logging
from browser_agent.use_cases.debug_bundle_writer import DebugBundleWriter
from browser_agent.use_cases.split_folder_reader import SplitFolderReader
from browser_agent.use_cases.split_folder_writer import SplitFolderWriter
from browser_agent.use_cases.task_discover_use_case import TaskDiscoverUseCase

DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."


class DiscoverTaskDriver:
    """Step-0 driver: task → discover agent → split folders under flow/."""

    def __init__(self) -> None:
        self._task_reader: TaskReader = TaskReader(DEFAULT_PROMPT)

    def run(self, argv: list[str]) -> int:
        """Configure logging, run the discover flow, return the process exit code."""
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
            logger.exception("discover driver failed")
            return 2
        finally:
            await self._cleanup(guard, heartbeat, watchdog, run_path)
            _safe(_write_debug_bundle, run, run_path, outcome, error_text)

    async def _run_flow(self, run: RunConfig, run_path, argv) -> int:
        from browser_agent.adapters.browser.clean_browser_launcher import kill_chromium_under

        kill_chromium_under(run_path)
        task = self._read_task(argv, run)
        reader = SplitFolderReader(run_path)
        existing = reader.read()
        logger.info(
            "discover driver starting task_tokens={n} run={run} existing_splits={s}",
            n=len(task) // 4,
            run=run.name,
            s=len(existing),
        )
        loop = DiscoverCoverageLoop(
            run_pass=lambda: self._discover(task, reader.context(), run, run_path),
            write_pass=lambda plan: self._write_splits(plan, reader, run_path),
        )
        return await loop.run(existing)

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        return self._task_reader.read(argv, run)

    async def _discover(self, task: str, context: str, run: RunConfig, run_path) -> DiscoverPlan:
        deps = self._build_deps(run, run_path)
        use_case = TaskDiscoverUseCase(deps)
        try:
            return await use_case.execute(task, context=context)
        finally:
            await use_case.close()
            self._delete_profile(run_path)

    def _delete_profile(self, run_path) -> None:
        """Remove the discover profile once its browser session is closed.

        Called after every discover pass so a lingering profile never
        survives into the next pass, a crash, or a hard kill. ``_safe``
        logging (not retrying) when Chromium still holds the directory.
        """
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir

        _safe(delete_profile_dir, run_path / "profile")

    def _build_deps(self, run: RunConfig, run_path):
        from browser_agent.adapters.browser.zendriver_browser_session import (
            ZendriverBrowserSession,
        )
        from browser_agent.adapters.execution.in_process_script_runner_adapter import (
            InProcessScriptRunnerAdapter,
        )
        from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
            CurlCffiPdfDownloaderAdapter,
        )
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.configuration import DISCOVER_MAX_EXPLORE_CALLS, ZENDRIVER_HEADLESS
        from browser_agent.use_cases.agent_deps import AgentDeps

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )
        deps = AgentDeps(
            llm=build_llm(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=run_path / "metadata.db",
                task_slug=run.name,
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(run_path / "downloads"),
        )
        # Dense boundary verification needs far more explore calls than the
        # generic MAX_EXPLORE_CALLS backstop; the discover agent opens every
        # boundary page of every chunk it emits.
        deps.explore_limit = DISCOVER_MAX_EXPLORE_CALLS
        return deps

    def _write_splits(self, plan: DiscoverPlan, reader: SplitFolderReader, run_path) -> list[str]:
        existing_names = {split.folder_name for split in reader.read()}
        created = SplitFolderWriter(run_path).write(plan, reader.next_order(), existing_names)
        logger.info("discover driver created folders={c}", c=created)
        return created

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


def _safe(fn, *args) -> None:
    """Run ``fn(*args)``, logging (not raising) any failure so cleanup continues."""
    try:
        fn(*args)
    except Exception:
        logger.exception("cleanup step failed: {fn}", fn=getattr(fn, "__name__", repr(fn)))


def _write_debug_bundle(run: RunConfig, run_path, outcome: str, error_text: str) -> None:
    DebugBundleWriter(run_path).write(run, outcome, error_text)


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(DiscoverTaskDriver().run(sys.argv))


if __name__ == "__main__":
    main()
