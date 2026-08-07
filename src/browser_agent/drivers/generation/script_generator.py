"""Build agent dependencies and orchestrate the three-agent script generation.

Wires the :class:`ZendriverBrowserSession` (shared with the
:class:`InProcessScriptRunnerAdapter`), the
:class:`OllamaAdapter` LLM, and the
:class:`CurlCffiPdfDownloaderAdapter` PDF downloader into
:class:`AgentDeps` instances for each agent. The Explorer starts the
browser session; the Discovery Writer and Processing Writer reuse it.
The session stays open until :meth:`close_all` tears it down.
"""

from __future__ import annotations

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
        self._session: ZendriverBrowserSession | None = None

    async def generate_split(
        self,
        task: str,
        run_path: Path,
        context: str = "",
    ) -> tuple[TaskSplit, ExploreSiteUseCase]:
        """Run the Explorer agent; return the split + live explorer use case."""
        session = self._build_session(run_path)
        self._session = session
        deps = self._build_deps(session, run_path, EXPLORER_MAX_LLM_CALLS)
        use_case = ExploreSiteUseCase(deps)
        prompt = f"{context}\n\n---\n\n{task}" if context else task
        split = await use_case.execute(CodeGenerationRequest(task=prompt))
        return split, use_case

    async def generate_discovery(
        self,
        split: TaskSplit,
        run_path: Path,
    ) -> tuple[GeneratedScript, GenerateDiscoveryScriptUseCase]:
        """Run the Discovery Writer agent with the split's discovery prompt."""
        assert self._session is not None
        deps = self._build_deps(self._session, run_path, WRITER_MAX_LLM_CALLS)
        use_case = GenerateDiscoveryScriptUseCase(deps)
        script = await use_case.execute(split)
        return script, use_case

    async def generate_processing(
        self,
        split: TaskSplit,
        run_path: Path,
        concurrency: str = "",
    ) -> tuple[GeneratedScript, GenerateProcessingScriptUseCase]:
        """Run the Processing Writer agent with the split's processing prompt."""
        assert self._session is not None
        deps = self._build_deps(self._session, run_path, WRITER_MAX_LLM_CALLS)
        use_case = GenerateProcessingScriptUseCase(deps)
        script = await use_case.execute(split, concurrency=concurrency)
        return script, use_case

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

    async def close_all(self, explorer_use_case: ExploreSiteUseCase) -> None:
        """Close the shared browser session after all agents finish."""
        await explorer_use_case.close()
        self._session = None

    def _build_session(self, run_path: Path) -> ZendriverBrowserSession:
        """Return a :class:`ZendriverBrowserSession` rooted in the run's profile dir."""
        return ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
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
