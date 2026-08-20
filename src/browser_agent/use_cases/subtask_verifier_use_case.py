"""Per-subtask verification: reconciler + probe + LLM agent, scoped by task_slug."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from browser_agent.domain.script_tools_feedback import ScriptToolsFeedback
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.metadata_db import parse_row_data, query_rows
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder


def _missing_html_records(
    db_path: Path,
    downloads_path: Path,
    task_slug: str,
    require_html_files: bool,
) -> list[str]:
    """Return source_urls whose downloaded document lacks the required HTML.

    Deterministic, no-LLM gate on the ``metadata`` table. For every row of
    this subtask that downloaded a document file on disk:

    * When ``require_html_files`` is True (registry/related-document flow),
      the record MUST carry a non-empty ``html_filename`` whose file exists
      under ``downloads/``. A bare ``source_html`` row snippet is metadata,
      not the required HTML file, so it does not satisfy the gate.
    * When ``require_html_files`` is False, a non-empty ``source_html`` row
      snippet is accepted in lieu of an on-disk HTML file.

    Rows without a downloaded document are skipped — there is nothing to
    attach the HTML to. A missing DB table yields no findings (empty run).
    """
    try:
        rows = query_rows(db_path, task_slug)
    except sqlite3.OperationalError:
        return []
    missing: list[str] = []
    for source_url, _slug, data_json in rows:
        data = parse_row_data(data_json)
        if not (data.get("pdf_filename") or "").strip():
            continue
        html_name = (data.get("html_filename") or "").strip()
        if html_name:
            if (downloads_path / html_name).is_file():
                continue
            missing.append(f"{source_url} (html_filename set but file missing from downloads/)")
            continue
        if not require_html_files and (data.get("source_html") or "").strip():
            continue
        missing.append(
            f"{source_url} (downloaded document has no captured HTML; set html_filename to a save_page_html result)"
        )
    return missing


class SubtaskVerifierUseCase:
    """Run the verification pipeline for one subtask — scoped by task_slug."""

    def __init__(
        self,
        db_path: Path,
        downloads_path: Path,
        run_path: Path,
        require_html_files: bool = False,
    ) -> None:
        self._db_path = db_path
        self._downloads_path = downloads_path
        self._run_path = run_path
        self._require_html_files = require_html_files

    async def verify(self, subtask: SubtaskSpec, state_store) -> VerificationReport:
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.llm.opencode_zen_adapter import OpenCodeZenAdapter
        from browser_agent.adapters.execution.subprocess_read_script_runner import SubprocessReadScriptRunner
        from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
        from browser_agent.use_cases.verify_downloads_use_case import VerifyDownloadsUseCase
        from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
        from browser_agent.use_cases.verification_report_writer import VerificationReportWriter
        from browser_agent.use_cases.probe_corpus_verifier import ProbeCorpusVerifier

        # Deterministic: reconciler scoped by task_slug
        reconciler = ReconcileDownloadsUseCase(
            self._db_path,
            self._downloads_path,
            task_slug=subtask.subtask_id,
        )
        _per_row, _findings = reconciler.reconcile()

        # Probe stage
        probe_results: list = []
        if subtask.sample_document_urls:
            probe_report = ProbeCorpusVerifier(self._db_path).verify(
                subtask.sample_document_urls,
            )
            probe_results = probe_report.results

        # LLM stage
        gap_map = ScrapingGapMapBuilder(self._db_path).build()
        request = VerificationRequest(
            task_prompt=subtask.description,
            discovery_script="",
            processing_script="",
            gap_map=gap_map,
            reconciler_inventory="",
        )

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=self._run_path / "profile_builder",
        )
        model = OpenCodeZenAdapter().get_model()
        deps = VerificationAgentDeps(
            browser_session=session,
            db_path=self._db_path,
            downloads_path=self._downloads_path,
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=_pdf_count(),
            query_db_limit=_query_limit(),
            script_run_limit=_script_run_limit(),
        )
        report = await VerifyDownloadsUseCase(deps, model).execute(request)
        if probe_results:
            report.probe_results = probe_results

        # Deterministic HTML-capture gate (no LLM). Marks the run failed when
        # a registry/related-document flow hit a downloaded document with no
        # linked HTML file on disk.
        missing_html = _missing_html_records(
            self._db_path,
            self._downloads_path,
            subtask.subtask_id,
            self._require_html_files,
        )
        report.html_required = self._require_html_files
        report.html_capture_complete = not missing_html
        report.html_missing_records = missing_html
        if missing_html:
            report.script_tools_improvements.append(
                f"{len(missing_html)} record(s) downloaded a document but captured no required HTML file "
                f"(html_filename empty or missing from downloads/). Re-run the processing script with "
                f"save_page_html + html_filename per record."
            )

        # Persist report in subtask dir
        subtask_report_dir = self._run_path / "flow" / "subtasks" / subtask.subtask_id
        subtask_report_dir.mkdir(parents=True, exist_ok=True)
        writer = VerificationReportWriter(self._run_path, output_dir=subtask_report_dir)
        _ = writer.write(report)

        # Derive ScriptToolsFeedback
        feedback = ScriptToolsFeedback(
            subtask_id=subtask.subtask_id,
            improvements=report.script_tools_improvements,
            summary=f"{len(report.script_tools_improvements)} improvements from verification",
        )
        state_store.write_report(subtask.subtask_id, "script_tools_feedback", feedback)

        return report

    @staticmethod
    def passed(report: VerificationReport) -> bool:
        from browser_agent.domain.probe_result import ProbeVerdict

        coverage_ok = report.coverage_complete or (report.missing_count == 0 and not report.missing_coverage)
        probe_ok = not any(r.verdict is not ProbeVerdict.CAPTURED for r in report.probe_results)
        html_ok = (not report.html_required) or report.html_capture_complete
        return coverage_ok and probe_ok and html_ok


def _pdf_count() -> int:
    from browser_agent.configuration import VERIFICATION_PDF_COUNT

    return VERIFICATION_PDF_COUNT


def _query_limit() -> int:
    from browser_agent.configuration import VERIFICATION_QUERY_LIMIT

    return VERIFICATION_QUERY_LIMIT


def _script_run_limit() -> int:
    from browser_agent.configuration import VERIFICATION_SCRIPT_RUN_LIMIT

    return VERIFICATION_SCRIPT_RUN_LIMIT
