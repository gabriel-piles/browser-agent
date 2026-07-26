"""Top-level driver for the download-verification agent (step 1).

Reads the active run from ``active_run.yaml``, builds a
:class:`VerificationAgentDeps` with a :class:`ZendriverBrowserSession`,
the run's ``metadata.db`` + ``downloads/`` paths, and a
:class:`SubprocessReadScriptRunner`, constructs a
:class:`VerificationRequest` from the run prompt + the latest step 0
script + a gap map of the DB, runs the
:class:`VerifyDownloadsUseCase` with the ``minimax-m3:cloud`` model,
and writes ``verification_report.md`` into the run directory.

Usage:
    python -m browser_agent.drivers.step_1_verify_downloaded_pdfs
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.subprocess_read_script_runner import (
    SubprocessReadScriptRunner,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.configuration import (
    VERIFICATION_MODEL,
    VERIFICATION_PDF_COUNT,
    VERIFICATION_SCRIPT_RUN_LIMIT,
    ZENDRIVER_HEADLESS,
)
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
from browser_agent.use_cases.verification_report_writer import VerificationReportWriter
from browser_agent.use_cases.verify_downloads_use_case import VerifyDownloadsUseCase

SCRIPTS_DIRNAME = "scripts"


class VerifyDownloadsDriver:
    """End-to-end driver: run the verification agent, write the report."""

    def run(self) -> int:
        """Configure logging, run the async pipeline, return the exit code."""
        configure_logging()
        return asyncio.run(self._run_async())

    async def _run_async(self) -> int:
        """Load the active run, build deps + request, run, write report."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        logger.info("verification driver starting run={run}", run=run.name)
        script = self._read_latest_script(run_path)
        if script is None:
            return 1
        deps = self._build_deps(run_path)
        request = self._build_request(run, script, run_path)
        model = OllamaAdapter(model=VERIFICATION_MODEL).get_model()
        report = await VerifyDownloadsUseCase(deps, model).execute(request)
        path = VerificationReportWriter(run_path).write(report)
        logger.info("verification report written to {path}", path=path)
        return 0

    def _build_deps(self, run_path: Path) -> VerificationAgentDeps:
        """Wire the browser session, DB/downloads paths, and script runner into deps."""
        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )
        return VerificationAgentDeps(
            browser_session=session,
            db_path=run_path / "metadata.db",
            downloads_path=run_path / "downloads",
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=VERIFICATION_PDF_COUNT,
            script_run_limit=VERIFICATION_SCRIPT_RUN_LIMIT,
        )

    def _build_request(self, run, script: str, run_path: Path) -> VerificationRequest:
        """Build the verification request from the run prompt, script, and gap map."""
        gap_map = ScrapingGapMapBuilder(run_path / "metadata.db").build()
        return VerificationRequest(
            task_prompt=run.prompt,
            generated_script=script,
            gap_map=gap_map,
        )

    def _read_latest_script(self, run_path: Path) -> str | None:
        """Return the most recent ``scripts/*.py`` source, or None."""
        scripts_dir = run_path / SCRIPTS_DIRNAME
        if not scripts_dir.is_dir():
            logger.warning("no scripts directory at {dir}", dir=scripts_dir)
            return None
        scripts = sorted(scripts_dir.glob("*.py"), key=lambda p: p.stat().st_mtime)
        if not scripts:
            logger.warning("no step 0 scripts found in {dir}", dir=scripts_dir)
            return None
        return scripts[-1].read_text(encoding="utf-8")


def main() -> None:
    """Module entry point."""
    raise SystemExit(VerifyDownloadsDriver().run())


if __name__ == "__main__":
    main()
