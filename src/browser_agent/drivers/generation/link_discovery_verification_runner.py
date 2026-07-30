"""Driver helper that runs the link-discovery-verification agent and emits its script.

Builds a fresh :class:`ZendriverBrowserSession` + :class:`AgentDeps`
(separate from the main generator's session, which is already closed
by the time this runs), runs
:class:`GenerateLinkDiscoveryVerificationUseCase` with the original
task and the main generated script as context, writes the
verification script to ``<run>/scripts/<date>__verify_discovery__<slug>.py``
(the ``script_tools/`` copy is already present from step 0), and runs
a best-effort smoke test on it. Failures are logged but never fail
step 0 — the verification script is a secondary artifact.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from loguru import logger

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
from browser_agent.domain.link_discovery_verification_script import (
    LinkDiscoveryVerificationScript,
)
from browser_agent.drivers.generation.script_smoke_tester import (
    log_smoke_test_result,
    smoke_test_script,
)
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.generate_link_discovery_verification_use_case import (
    GenerateLinkDiscoveryVerificationUseCase,
)

_DISCOVERY_SUFFIX = "__verify_discovery"
_TASK_WORD_LIMIT = 8


class LinkDiscoveryVerificationRunner:
    """Run the link-discovery-verification agent, emit its script, smoke-test it."""

    async def run(self, task: str, original_script_code: str, run_path: Path) -> None:
        """Generate + emit + smoke-test the discovery-verification script (best-effort)."""
        session = self._build_session(run_path)
        deps = self._build_deps(session, run_path)
        use_case = GenerateLinkDiscoveryVerificationUseCase(deps)
        prompt = self._build_prompt(task, original_script_code)
        try:
            script = await use_case.execute(prompt)
        except Exception:
            logger.exception("link-discovery-verification agent failed")
            await use_case.close()
            return
        path = self._write_script(script, run_path, task)
        await use_case.close()
        await self._smoke_test(path)

    def _build_session(self, run_path: Path) -> ZendriverBrowserSession:
        return ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )

    def _build_deps(self, session: ZendriverBrowserSession, run_path: Path) -> AgentDeps:
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

    def _build_prompt(self, task: str, original_script_code: str) -> str:
        return (
            f"Original task:\n{task}\n\n"
            "Main scraper script (already generated, for reference — reuse its "
            "filter selectors and navigation strategy, but verify that its LINK "
            "DISCOVERY is complete: re-walk the site, run the full scroll / "
            "load-more / dropdown / lazy-load loop per filter value, and report "
            "discovered vs site-advertised counts per path):\n"
            f"```python\n{original_script_code}\n```\n\n"
            "Generate a standalone verification script that confirms the main "
            "scraper discovered ALL PDF links — flagging any filter value where "
            "it under-collected (e.g. stopped at 10 when the site exposes 55)."
        )

    def _write_script(
        self,
        script: LinkDiscoveryVerificationScript,
        run_path: Path,
        task: str,
    ) -> Path:
        scripts_dir = run_path / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today().strftime("%Y_%m_%d")
        path = scripts_dir / f"{today}{_DISCOVERY_SUFFIX}__{_slug(task)}.py"
        path.write_text(script.python_code, encoding="utf-8")
        logger.info("link-discovery-verification script emitted at {path}", path=path)
        return path

    async def _smoke_test(self, path: Path) -> None:
        result = await smoke_test_script(path)
        log_smoke_test_result(result, path, attempt=1)
        if not result.success:
            logger.warning(
                "link-discovery-verification smoke test FAILED — script kept at {path}",
                path=path,
            )


def _slug(task: str) -> str:
    """Return a filesystem-safe slug derived from the first words of ``task``."""
    words = task.split()[:_TASK_WORD_LIMIT]
    raw = "_".join(words)
    return "".join(c if c.isalnum() else "_" for c in raw.lower()).strip("_") or "generated"
