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
import json
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
    VERIFICATION_QUERY_LIMIT,
    VERIFICATION_SCRIPT_RUN_LIMIT,
    ZENDRIVER_HEADLESS,
)
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
from browser_agent.use_cases.reconciler_report_writer import ReconcilerReportWriter
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
        """Load the active run, reconcile, run agent, write report, return exit code."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        logger.info("verification driver starting run={run}", run=run.name)
        script_path = self._latest_script_path(run_path)
        if script_path is None:
            return 2
        script = script_path.read_text(encoding="utf-8")
        explanation = self._read_sidecar_explanation(script_path)
        try:
            reconciler_section = self._run_reconciler(run_path)
            deps = self._build_deps(run_path)
            request = self._build_request(run, script, run_path, explanation, reconciler_section)
            model = OllamaAdapter(model=VERIFICATION_MODEL).get_model()
            report = await VerifyDownloadsUseCase(deps, model).execute(request)
        except Exception as exc:
            logger.error("verification could not run: {exc}", exc=exc)
            return 2
        path = VerificationReportWriter(run_path).write(report)
        logger.info("verification report written to {path}", path=path)
        return self._exit_code(report)

    def _run_reconciler(self, run_path: Path) -> str:
        """Run the deterministic reconciler, persist it, return the markdown section."""
        db_path = run_path / "metadata.db"
        downloads_path = run_path / "downloads"
        per_row, findings = ReconcileDownloadsUseCase(db_path, downloads_path).reconcile()
        md_path, json_path = ReconcilerReportWriter(run_path).write(per_row, findings)
        logger.info("reconciler inventory written to {path}", path=md_path)
        logger.info("reconciler json written to {path}", path=json_path)
        return ReconcilerReportWriter(run_path).render_section(per_row, findings)

    @staticmethod
    def _exit_code(report: VerificationReport) -> int:
        """0 clean, 1 gaps found, 2 could not run."""
        if report.missing_count > 0 or report.missing_coverage:
            return 1
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
            query_db_limit=VERIFICATION_QUERY_LIMIT,
            script_run_limit=VERIFICATION_SCRIPT_RUN_LIMIT,
        )

    def _build_request(
        self,
        run,
        script: str,
        run_path: Path,
        explanation: str,
        reconciler_section: str,
    ) -> VerificationRequest:
        """Build the verification request from the run prompt, script, gap map, and reconciler."""
        gap_map = ScrapingGapMapBuilder(run_path / "metadata.db").build()
        return VerificationRequest(
            task_prompt=run.prompt,
            generated_script=script,
            gap_map=gap_map,
            step0_explanation=explanation,
            reconciler_inventory=reconciler_section,
        )

    def _latest_script_path(self, run_path: Path) -> Path | None:
        """Return the most recent ``scripts/*.py`` path, or None."""
        scripts_dir = run_path / SCRIPTS_DIRNAME
        if not scripts_dir.is_dir():
            logger.warning("no scripts directory at {dir}", dir=scripts_dir)
            return None
        scripts = sorted(scripts_dir.glob("*.py"), key=lambda p: p.stat().st_mtime)
        if not scripts:
            logger.warning("no step 0 scripts found in {dir}", dir=scripts_dir)
            return None
        return scripts[-1]

    @staticmethod
    def _read_sidecar_explanation(script_path: Path) -> str:
        """Return the ``explanation`` from the sidecar JSON, or empty string."""
        sidecar = script_path.with_suffix(".json")
        if not sidecar.is_file():
            return ""
        try:
            return json.loads(sidecar.read_text(encoding="utf-8")).get("explanation", "")
        except (ValueError, OSError):
            return ""


def main() -> None:
    """Module entry point."""
    raise SystemExit(VerifyDownloadsDriver().run())


if __name__ == "__main__":
    main()
