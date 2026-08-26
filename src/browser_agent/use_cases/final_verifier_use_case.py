"""Whole-run final verification: reconciler + gap map + LLM agent.

Ported from step_2_verify_downloaded_pdfs.py _run_async composition.
"""

from __future__ import annotations

from pathlib import Path


class FinalVerifierUseCase:
    """Run the full verification pipeline for the whole run (no task_slug scoping)."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    async def verify(self, task_prompt: str) -> None:
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.adapters.execution.subprocess_read_script_runner import SubprocessReadScriptRunner
        from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
        from browser_agent.use_cases.verify_downloads_use_case import VerifyDownloadsUseCase
        from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
        from browser_agent.use_cases.verification_report_writer import VerificationReportWriter
        from browser_agent.use_cases.reconciler_report_writer import ReconcilerReportWriter
        from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder
        from browser_agent.domain.verification_request import VerificationRequest

        db_path = self._run_path / "metadata.db"
        downloads_path = self._run_path / "downloads"

        # Reconciler (unscoped — whole run)
        reconciler = ReconcileDownloadsUseCase(db_path, downloads_path)
        per_row, findings = reconciler.reconcile()
        ReconcilerReportWriter(self._run_path).write(per_row, findings)

        # LLM stage
        gap_map = ScrapingGapMapBuilder(db_path).build()
        request = VerificationRequest(
            task_prompt=task_prompt,
            discovery_script="",
            processing_script="",
            gap_map=gap_map,
            reconciler_inventory="",
        )

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=self._run_path / "profile",
        )
        model = build_llm().get_model()
        deps = VerificationAgentDeps(
            browser_session=session,
            db_path=db_path,
            downloads_path=downloads_path,
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=_pdf_count(),
            query_db_limit=_query_limit(),
            script_run_limit=_script_run_limit(),
        )
        report = await VerifyDownloadsUseCase(deps, model).execute(request)
        delete_profile_dir(self._run_path / "profile")

        # Write report at run root
        writer = VerificationReportWriter(self._run_path)
        _ = writer.write(report)


def _pdf_count() -> int:
    from browser_agent.configuration import VERIFICATION_PDF_COUNT

    return VERIFICATION_PDF_COUNT


def _query_limit() -> int:
    from browser_agent.configuration import VERIFICATION_QUERY_LIMIT

    return VERIFICATION_QUERY_LIMIT


def _script_run_limit() -> int:
    from browser_agent.configuration import VERIFICATION_SCRIPT_RUN_LIMIT

    return VERIFICATION_SCRIPT_RUN_LIMIT
