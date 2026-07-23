"""Top-level driver for the scraping validation agent (step 4).

Reads the active run from ``runs.yaml``, builds a
:class:`ValidationAgentDeps` with a :class:`ZendriverBrowserSession`
and the run's ``metadata.db`` + ``downloads/`` paths, constructs a
:class:`ValidationRequest` from the run prompt + the latest step 0
script + a gap map of the DB, runs the
:class:`ValidateScrapingUseCase` with the ``minimax-m3:cloud`` model,
and writes ``validation_report.md`` into the run directory.

Usage:
    python -m browser_agent.drivers.step_4_validate_scraping
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.configuration import VALIDATION_MODEL, VALIDATION_PDF_COUNT, ZENDRIVER_HEADLESS
from browser_agent.domain.validation_request import ValidationRequest
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder
from browser_agent.use_cases.scraping_report_writer import ScrapingReportWriter
from browser_agent.use_cases.validate_scraping_use_case import ValidateScrapingUseCase
from browser_agent.use_cases.validation_agent_deps import ValidationAgentDeps

SCRIPTS_DIRNAME = "scripts"


class ValidateScrapingDriver:
    """End-to-end driver: run the validation agent, write the report."""

    def run(self) -> int:
        """Configure logging, run the async pipeline, return the exit code."""
        configure_logging()
        return asyncio.run(self._run_async())

    async def _run_async(self) -> int:
        """Load the active run, build deps + request, run, write report."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        logger.info("validation driver starting run={run}", run=run.name)
        script = self._read_latest_script(run_path)
        if script is None:
            return 1
        deps = self._build_deps(run_path)
        request = self._build_request(run, script, run_path)
        model = OllamaAdapter(model=VALIDATION_MODEL).get_model()
        report = await ValidateScrapingUseCase(deps, model).execute(request)
        path = ScrapingReportWriter(run_path).write(report)
        logger.info("validation report written to {path}", path=path)
        return 0

    def _build_deps(self, run_path: Path) -> ValidationAgentDeps:
        """Wire the browser session and DB/downloads paths into deps."""
        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )
        return ValidationAgentDeps(
            browser_session=session,
            db_path=run_path / "metadata.db",
            downloads_path=run_path / "downloads",
            pdf_check_limit=VALIDATION_PDF_COUNT,
        )

    def _build_request(self, run, script: str, run_path: Path) -> ValidationRequest:
        """Build the validation request from the run prompt, script, and gap map."""
        gap_map = ScrapingGapMapBuilder(run_path / "metadata.db").build()
        return ValidationRequest(
            task_prompt=run.prompt,
            generated_script=script,
            gap_map=gap_map,
        )

    def _read_latest_script(self, run_path: Path) -> str | None:
        """Return the most recent ``scripts/*.py`` source, or None."""
        scripts_dir = run_path / SCRIPTS_DIRNAME
        scripts = sorted(scripts_dir.glob("*.py"), key=lambda p: p.stat().st_mtime)
        if not scripts:
            logger.error("no script found in {dir}", dir=scripts_dir)
            return None
        return scripts[-1].read_text(encoding="utf-8")


def main() -> None:
    """Module entry point."""
    raise SystemExit(ValidateScrapingDriver().run())


if __name__ == "__main__":
    main()
