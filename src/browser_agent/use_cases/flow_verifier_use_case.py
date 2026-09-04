"""Flow verify agent: legacy download verification + the next-step decision.

Reuses the legacy verification machinery unchanged — the deterministic
reconciler scoped by task_slug, the probe corpus, the gap map, and the
independent LLM verify agent with its 5 tools — and extends the
structured output with the flow ``decision`` (rewrite_script /
add_extra_script / re_execute / accept). Discovery verification is
removed: every flow script is a processing script.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from browser_agent.domain.flow_subtask_spec import FlowSubtaskSpec
from browser_agent.domain.flow_verification_report import FlowVerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
from browser_agent.use_cases.reconciler_report_writer import ReconcilerReportWriter
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder


class FlowVerifierUseCase:
    """Verify one flow subtask's downloads and decide what happens next."""

    def __init__(
        self,
        db_path: Path,
        downloads_path: Path,
        run_path: Path,
        verification_dir: Path,
        require_html_files: bool = False,
        original_task: str = "",
        split_prompt: str = "",
    ) -> None:
        self._db_path: Path = db_path
        self._downloads_path: Path = downloads_path
        self._run_path: Path = run_path
        self._verification_dir: Path = verification_dir
        self._require_html_files: bool = require_html_files
        self._original_task: str = original_task
        self._split_prompt: str = split_prompt

    async def verify(
        self,
        spec: FlowSubtaskSpec,
        script_sources: list[str],
        execution_summary: str = "",
        previous_report: FlowVerificationReport | None = None,
    ) -> FlowVerificationReport:
        """Run the whole verification pipeline for one flow subtask.

        ``script_sources`` are the emitted scripts' sources (primary + any
        extras) — handed to the verify agent for root-causing.
        """
        from browser_agent.use_cases.flow_verification_gates import apply_processing_gates

        reconciler = ReconcileDownloadsUseCase(self._db_path, self._downloads_path, task_slug=spec.subtask_id)
        per_row, findings = reconciler.reconcile()
        _ = ReconcilerReportWriter(self._verification_dir).write(per_row, findings)

        gap_map = ScrapingGapMapBuilder(self._db_path).build(task_slug=spec.subtask_id)
        scope = (self._split_prompt or spec.description).strip()
        task_prompt = scope + (
            "\n\n## ORIGINAL TASK (context only — coverage is judged ONLY against the scope above; "
            "paths owned by other chunks/splits are NOT gaps here)\n" + self._original_task
            if self._original_task
            else ""
        )
        previous_decision = ""
        if previous_report is not None:
            previous_decision = f"action={previous_report.decision.action}\nfocus={previous_report.decision.focus}\nreasoning={previous_report.decision.reasoning}\nmissing_count={previous_report.missing_count} observed_pdf_total={previous_report.observed_pdf_total} expected_pdf_total={previous_report.expected_pdf_total}"
        request = VerificationRequest(
            task_prompt=task_prompt or spec.description,
            discovery_script="",
            processing_script="\n\n# --- next script ---\n\n".join(script_sources),
            gap_map=gap_map,
            reconciler_inventory=ReconcilerReportWriter(self._verification_dir).render_compact_section(per_row, findings),
            execution_summary=execution_summary,
            previous_decision=previous_decision,
        )
        report = await self._run_agent(request, spec.sample_document_urls)
        report = apply_processing_gates(
            report,
            db_path=self._db_path,
            downloads_path=self._downloads_path,
            subtask_id=spec.subtask_id,
            require_html_files=self._require_html_files,
        )
        self._persist(report)
        return report

    async def _run_agent(self, request: VerificationRequest, sample_urls: list[str]) -> FlowVerificationReport:
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.execution.subprocess_read_script_runner import SubprocessReadScriptRunner
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.use_cases.flow_verify_downloads_use_case import FlowVerifyDownloadsUseCase
        from browser_agent.use_cases.probe_corpus_verifier import ProbeCorpusVerifier
        from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps

        probe_results = ProbeCorpusVerifier(self._db_path).verify(sample_urls) if sample_urls else None
        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=self._run_path / "profile_verifier",
        )
        model = build_llm().get_model()
        deps = VerificationAgentDeps(
            browser_session=session,
            db_path=self._db_path,
            downloads_path=self._downloads_path,
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=self._pdf_count(),
            query_db_limit=self._query_limit(),
            script_run_limit=self._script_run_limit(),
        )
        report = await FlowVerifyDownloadsUseCase(deps, model).execute(request)
        delete_profile_dir(self._run_path / "profile_verifier")
        if probe_results is not None:
            report.probe_results = probe_results.results
        return report

    def _persist(self, report: FlowVerificationReport) -> None:
        from browser_agent.use_cases.verification_report_writer import VerificationReportWriter

        _ = VerificationReportWriter(self._run_path, output_dir=self._verification_dir).write(report)
        logger.info("flow verification report written dir={dir}", dir=self._verification_dir)

    @staticmethod
    def passed(report: FlowVerificationReport) -> bool:
        """Legacy pass/fail gates, unchanged (the decision is separate)."""
        from browser_agent.domain.probe_result import ProbeVerdict

        if report.missing_count > 0 or report.missing_coverage:
            return False
        if report.expected_pdf_total > 0 and report.observed_pdf_total < report.expected_pdf_total:
            return False
        coverage_ok = report.coverage_complete or (report.missing_count == 0 and not report.missing_coverage)
        probe_ok = not any(r.verdict is not ProbeVerdict.CAPTURED for r in report.probe_results)
        html_ok = (not report.html_required) or report.html_capture_complete
        return coverage_ok and probe_ok and html_ok

    @staticmethod
    def _pdf_count() -> int:
        from browser_agent.configuration import VERIFICATION_PDF_COUNT

        return VERIFICATION_PDF_COUNT

    @staticmethod
    def _query_limit() -> int:
        from browser_agent.configuration import VERIFICATION_QUERY_LIMIT

        return VERIFICATION_QUERY_LIMIT

    @staticmethod
    def _script_run_limit() -> int:
        from browser_agent.configuration import VERIFICATION_SCRIPT_RUN_LIMIT

        return VERIFICATION_SCRIPT_RUN_LIMIT
