"""Build agent deps and run the three-agent script generation.

The Explorer runs first in its own browser session (closed after the
split is produced). The Discovery Writer and Processing Writer then
run concurrently, each with its own :class:`ZendriverBrowserSession`
and profile dir so two Chromium instances don't contend on a single
locked user-data-dir. :meth:`close_all` tears both writer sessions down.
"""

import asyncio

from pathlib import Path

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
    CurlCffiPdfDownloaderAdapter,
)
from browser_agent.adapters.execution.in_process_script_runner_adapter import (
    InProcessScriptRunnerAdapter,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.configuration import EXPLORER_MAX_LLM_CALLS, WRITER_MAX_LLM_CALLS, ZENDRIVER_HEADLESS
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.task_split import TaskSplit
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.explore_site_use_case import ExploreSiteUseCase
from browser_agent.use_cases.generate_discovery_script_use_case import (
    GenerateDiscoveryScriptUseCase,
)
from browser_agent.use_cases.generate_processing_script_use_case import (
    GenerateProcessingScriptUseCase,
)


class ScriptGenerator:
    """Build deps + run the three-agent script-generation pipeline for one task."""

    def __init__(self) -> None:
        self._discovery_session: ZendriverBrowserSession | None = None
        self._processing_session: ZendriverBrowserSession | None = None

    async def generate_split(
        self,
        task: str,
        run_path: Path,
        context: str = "",
    ) -> TaskSplit:
        """Run the Explorer agent; return the split (session closed immediately)."""
        session = self._build_session(run_path / "profile")
        deps = self._build_deps(session, run_path, EXPLORER_MAX_LLM_CALLS)
        use_case = ExploreSiteUseCase(deps)
        prompt = f"{context}\n\n---\n\n{task}" if context else task
        try:
            return await use_case.execute(CodeGenerationRequest(task=prompt))
        finally:
            await use_case.close()

    async def generate_writers_concurrent(
        self,
        split: TaskSplit,
        run_path: Path,
        concurrency: str = "",
    ) -> tuple[
        GeneratedScript | None,
        GenerateDiscoveryScriptUseCase | None,
        GeneratedScript,
        GenerateProcessingScriptUseCase,
    ]:
        """Run the Discovery and Processing writers concurrently with separate sessions."""
        try:
            async with asyncio.TaskGroup() as tg:
                if split.needs_discovery:
                    disc = tg.create_task(self._discovery_task(split, run_path))
                proc = tg.create_task(self._processing_task(split, run_path, concurrency))
            disc_result = disc.result() if split.needs_discovery else (None, None)
            return (*disc_result, *proc.result())
        except Exception:
            await self.close_all()
            raise

    async def _discovery_task(
        self,
        split: TaskSplit,
        run_path: Path,
    ) -> tuple[GeneratedScript, GenerateDiscoveryScriptUseCase]:
        """Build + run the Discovery Writer in its own browser session."""
        self._discovery_session = self._build_session(run_path / "profile_discovery")
        deps = self._build_deps(self._discovery_session, run_path, WRITER_MAX_LLM_CALLS)
        use_case = GenerateDiscoveryScriptUseCase(deps)
        await self._discovery_session.start()
        return await use_case.execute(split), use_case

    async def _processing_task(
        self,
        split: TaskSplit,
        run_path: Path,
        concurrency: str = "",
    ) -> tuple[GeneratedScript, GenerateProcessingScriptUseCase]:
        """Build + run the Processing Writer in its own browser session."""
        self._processing_session = self._build_session(run_path / "profile_processing")
        deps = self._build_deps(self._processing_session, run_path, WRITER_MAX_LLM_CALLS)
        use_case = GenerateProcessingScriptUseCase(deps)
        await self._processing_session.start()
        return await use_case.execute(split, concurrency=concurrency), use_case

    @staticmethod
    async def repair_discovery(
        use_case: GenerateDiscoveryScriptUseCase,
        feedback: str,
    ) -> GeneratedScript:
        """Run a repair turn on the discovery writer with ``feedback``."""
        return await use_case.repair(feedback)

    @staticmethod
    async def repair_processing(
        use_case: GenerateProcessingScriptUseCase,
        feedback: str,
    ) -> GeneratedScript:
        """Run a repair turn on the processing writer with ``feedback``."""
        return await use_case.repair(feedback)

    async def close_all(self) -> None:
        """Close both writer browser sessions; idempotent."""
        if self._discovery_session is not None:
            await self._discovery_session.close()
            self._discovery_session = None
        if self._processing_session is not None:
            await self._processing_session.close()
            self._processing_session = None

    @property
    def discovery_session(self) -> ZendriverBrowserSession | None:
        """Return the live discovery writer's session (for the DiscoveryAuditor)."""
        return self._discovery_session

    def _build_session(self, profile_dir: Path) -> ZendriverBrowserSession:
        """Return a :class:`ZendriverBrowserSession` rooted in ``profile_dir``."""
        return ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=profile_dir,
        )

    def _build_deps(
        self,
        session: ZendriverBrowserSession,
        run_path: Path,
        call_budget: int,
    ) -> AgentDeps:
        """Wire LLM, browser, script runner and PDF downloader into :class:`AgentDeps`."""
        return AgentDeps(
            llm=OllamaAdapter(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=run_path / "metadata.db",
                task_slug=run_path.name,
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(
                downloads_path=run_path / "downloads",
            ),
            call_budget=call_budget,
        )
