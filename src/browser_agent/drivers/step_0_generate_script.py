"""Top-level driver class for the Zendriver scraping flow.

Reads a task from argv (or the bundled default), runs the full flow
orchestrator: plan → subtask pipeline → final verification. All agent
outputs are persisted as pydantic reports in the run folder.

Usage:
    python -m browser_agent.drivers.step_0_generate_script "<task>"
    python -m browser_agent.drivers.step_0_generate_script --stdin < task.txt
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.drivers.generation.script_emitter import ScriptEmitter
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.drivers.flow.flow_paths import FlowPaths
from browser_agent.drivers.flow.scrape_flow import ScrapeFlow
from browser_agent.drivers.flow.subtask_pipeline import SubtaskPipeline
from browser_agent.domain.run_config import RunConfig
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter
from browser_agent.use_cases.final_verifier_use_case import FinalVerifierUseCase
from browser_agent.use_cases.flow_state_store import FlowStateStore
from browser_agent.use_cases.orchestrator_use_case import OrchestratorUseCase
from browser_agent.use_cases.subtask_executor import SubtaskExecutor
from browser_agent.use_cases.subtask_verifier_use_case import SubtaskVerifierUseCase
from browser_agent.use_cases.task_planner_use_case import TaskPlannerUseCase

DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."


class GenerateScriptDriver:
    """End-to-end driver: task → flow orchestrator → scripts → verification."""

    def __init__(self) -> None:
        self._task_reader: TaskReader = TaskReader(DEFAULT_PROMPT)

    def run(self, argv: list[str]) -> int:
        """Configure logging, run the async pipeline, return the process exit code."""
        configure_logging()
        return asyncio.run(self._run_async(argv))

    async def _run_async(self, argv: list[str]) -> int:
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        ScriptToolsCopier().copy(run_path)

        task = self._read_task(argv, run)
        logger.info(
            "flow driver starting task_tokens={n} run={run}",
            n=len(task) // 4,
            run=run.name,
        )

        flow_paths = FlowPaths(run_path)
        state_store = FlowStateStore(flow_paths)

        path_builder = ScriptPathBuilder(run_path)
        emitter = ScriptEmitter(path_builder)
        # Registry/related-document runs (scraper_registry_template set) require a
        # saved HTML file per downloaded document, not just a source_html snippet.
        require_html_files = bool(run.scraper_registry_template)
        linter = EmittedScriptLinter(require_html_files=require_html_files)
        executor = SubtaskExecutor()
        verifier = SubtaskVerifierUseCase(
            db_path=run_path / "metadata.db",
            downloads_path=run_path / "downloads",
            run_path=run_path,
            require_html_files=require_html_files,
        )
        pipeline = SubtaskPipeline(flow_paths, emitter, linter, state_store, executor, verifier)
        orchestrator = OrchestratorUseCase()
        final_verifier = FinalVerifierUseCase(run_path)

        def planner_factory():
            from browser_agent.adapters.browser.zendriver_browser_session import (
                ZendriverBrowserSession,
            )
            from browser_agent.adapters.execution.in_process_script_runner_adapter import (
                InProcessScriptRunnerAdapter,
            )
            from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
                CurlCffiPdfDownloaderAdapter,
            )
            from browser_agent.adapters.llm.opencode_zen_adapter import OpenCodeZenAdapter
            from browser_agent.configuration import ZENDRIVER_HEADLESS

            session = ZendriverBrowserSession(
                headless=ZENDRIVER_HEADLESS,
                user_data_dir=run_path / "profile",
            )
            deps = AgentDeps(
                llm=OpenCodeZenAdapter(),
                browser_session=session,
                script_runner=InProcessScriptRunnerAdapter(
                    browser_session=session,
                    metadata_db_path=run_path / "metadata.db",
                    task_slug=run.name,
                ),
                pdf_downloader=CurlCffiPdfDownloaderAdapter(),
            )
            return TaskPlannerUseCase(deps)

        flow = ScrapeFlow(
            run_path,
            flow_paths,
            state_store,
            planner_factory,
            orchestrator,
            pipeline,
            final_verifier,
        )

        try:
            return await flow.run(task)
        except Exception:
            logger.exception("flow driver failed")
            return 2

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        return self._task_reader.read(argv, run)


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
