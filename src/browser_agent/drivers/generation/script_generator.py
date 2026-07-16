"""Build the agent dependencies and call the script-generation use case.

Wires the :class:`ZendriverBrowserSession` (shared with the
:class:`InProcessScriptRunnerAdapter`), the
:class:`OllamaAdapter` LLM, and the
:class:`CurlCffiPdfDownloaderAdapter` PDF downloader into an
:class:`AgentDeps` and hands the resulting
:class:`CodeGenerationRequest` to the
:class:`GenerateZendriverScriptUseCase`. The returned
:class:`GeneratedScript` is everything the
:class:`ScriptEmitter` needs to write the on-disk artifact.
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
from browser_agent.configuration import ZENDRIVER_HEADLESS
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.generate_zendriver_script_use_case import (
    GenerateZendriverScriptUseCase,
)


class ScriptGenerator:
    """Build deps + call the script-generation use case for one task."""

    async def generate(self, task: str, run_path: Path) -> GeneratedScript:
        """Run the agent for ``task`` and return the structured result."""
        session = self._build_session(run_path)
        deps = self._build_deps(session, run_path)
        return await GenerateZendriverScriptUseCase(deps).execute(CodeGenerationRequest(task=task))

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
        )
