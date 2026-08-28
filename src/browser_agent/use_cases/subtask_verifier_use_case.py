"""Per-subtask verification: reconciler + probe + LLM agent, scoped by task_slug."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from loguru import logger
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps

from browser_agent.domain.discovery_manifest import DiscoveryManifest
from browser_agent.domain.discovery_verification_request import DiscoveryVerificationRequest
from browser_agent.domain.missing_coverage import MissingCoverage
from browser_agent.domain.script_execution_report import ScriptExecutionReport
from browser_agent.domain.script_tools_feedback import ScriptToolsFeedback
from browser_agent.domain.subtask_record import SubtaskRecord
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.discovery_manifest_parser import (
    extract_manifest_detailed,
    parse_discovery_stdout,
)
from browser_agent.use_cases.metadata_db import (
    count_discovered_links,
    discovered_link_counts,
    parse_row_data,
    query_rows,
)
from browser_agent.use_cases.html_row_association import html_contains_record, row_needle
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder


def _html_content_problem(data: dict, downloads_path: Path) -> str | None:
    """Return a reason string when the row's core_html_filename exists but does not contain its document_ref."""
    html_name = (data.get("core_html_filename") or "").strip()
    if not html_name:
        return None
    path = downloads_path / html_name
    if not path.is_file():
        return None  # existence already reported by the caller
    needle = row_needle(data)
    if not needle or html_contains_record(path, needle):
        return None
    return "core_html_filename file does not contain this row's document_ref (wrong-page/shared capture)"


def _missing_html_records(
    db_path: Path,
    downloads_path: Path,
    task_slug: str,
    require_html_files: bool,
) -> list[str]:
    """Return core_ids whose downloaded document lacks the required HTML.

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
    except sqlite3.OperationalError as exc:
        logger.warning("_missing_html_records: query_rows failed for {slug}: {err}", slug=task_slug, err=exc)
        return []
    missing: list[str] = []
    for core_id, _slug, data_json in rows:
        data = parse_row_data(data_json)
        if not (data.get("core_pdf_filename") or "").strip():
            continue
        html_name = (data.get("core_html_filename") or "").strip()
        if html_name:
            if (downloads_path / html_name).is_file():
                problem = _html_content_problem(data, downloads_path)
                if problem is None:
                    continue
                missing.append(f"{core_id} ({problem})")
                continue
        if not require_html_files and (data.get("core_source_html") or "").strip():
            continue
        missing.append(
            f"{core_id} (downloaded document has no captured HTML; set core_html_filename to a save_page_html result)"
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
        if subtask.kind == "discovery":
            return await self._verify_discovery(subtask, state_store)
        return await self._verify_processing(subtask, state_store)

    async def _verify_processing(self, subtask: SubtaskSpec, state_store) -> VerificationReport:
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.adapters.execution.subprocess_read_script_runner import SubprocessReadScriptRunner
        from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
        from browser_agent.use_cases.verify_downloads_use_case import VerifyDownloadsUseCase
        from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
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
            user_data_dir=self._run_path / "profile_verifier",
        )
        model = build_llm().get_model()
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
        delete_profile_dir(self._run_path / "profile_verifier")
        if probe_results:
            report.probe_results = probe_results

        # Deterministic check: if discovered_links has rows but metadata for this subtask has 0 rows, fail verification
        try:
            disc_total = count_discovered_links(self._db_path)
            subtask_rows = query_rows(self._db_path, subtask.subtask_id)
            if disc_total > 0 and len(subtask_rows) == 0:
                report.coverage_complete = False
                report.missing_count = max(report.missing_count, 1)
                report.missing_coverage.append(
                    MissingCoverage(
                        navigation_path=f"processing {subtask.subtask_id}",
                        expected=f">0 metadata records from {disc_total} discovered links",
                        actual="0 metadata records saved",
                        reason="processing script processed 0 records despite discovered links existing",
                        step_0_fix=f"Ensure subtask {subtask.subtask_id} loads and processes its discovered links without starvation.",
                    )
                )
        except Exception as exc:
            logger.exception("coverage gate errored: {exc}", exc=exc)
            report.coverage_complete = False
            report.missing_coverage.append(
                MissingCoverage(
                    navigation_path=f"processing {subtask.subtask_id}",
                    expected="coverage gate to run",
                    actual=f"coverage gate errored: {exc}",
                    reason="the deterministic coverage check crashed",
                    step_0_fix=f"Ensure subtask {subtask.subtask_id} saves records and that metadata.db is accessible.",
                )
            )

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

        self._persist_report(report, subtask, state_store)

        return report

    async def _verify_discovery(self, subtask: SubtaskSpec, state_store) -> VerificationReport:
        from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir

        source, exec_report = self._load_discovery_inputs(subtask, state_store)
        gate = _discovery_gate(source, count_discovered_links(self._db_path))
        if gate.failure is not None:
            self._persist_report(gate.failure, subtask, state_store)
            return gate.failure
        assert gate.manifest is not None and source is not None  # gate guarantees both
        found, saved, _total = parse_discovery_stdout(exec_report.output_tail if exec_report else "")
        db_counts = discovered_link_counts(self._db_path)
        gaps = _deterministic_discovery_gaps(found, saved, db_counts)
        request = self._build_discovery_request(subtask, source, gate.manifest, found, saved, db_counts)
        report = await self._run_discovery_agent(request)
        delete_profile_dir(self._run_path / "profile_verifier")
        report = _merge_deterministic_gaps(report, gaps)
        self._persist_report(report, subtask, state_store)
        return report

    def _load_discovery_inputs(self, subtask: SubtaskSpec, state_store) -> tuple[str | None, Any]:
        """Return ``(script_source | None, execution_report | None)`` for the branch.

        The script source is resolved from the execution report's ``script_path``
        first: it is the exact script that produced the stdout being verified.
        The record in ``state.json`` is stale during the pipeline (persisted only
        after the pipeline returns), so reading it here would audit the previous
        attempt's script — or nothing at all on the first attempt.
        """
        exec_report = None
        if state_store is not None:
            exec_report = state_store.read_report(
                subtask.subtask_id,
                "execution_report",
                ScriptExecutionReport,
            )
        path = self._resolve_discovery_script_path(subtask, state_store, exec_report)
        if path is None:
            return None, exec_report
        return path.read_text(encoding="utf-8"), exec_report

    def _resolve_discovery_script_path(
        self,
        subtask: SubtaskSpec,
        state_store,
        exec_report: ScriptExecutionReport | None,
    ) -> Path | None:
        """Resolve the discovery script source, preferring the execution report."""
        candidates: list[str] = []
        if exec_report is not None and exec_report.script_path:
            candidates.append(exec_report.script_path)
        record = _find_record(state_store, subtask.subtask_id)
        if record is not None and record.script_path:
            candidates.append(record.script_path)
        for raw in candidates:
            path = Path(raw)
            if not path.is_absolute():
                path = self._run_path / path
            if path.is_file():
                return path
        return None

    def _build_discovery_request(
        self,
        subtask: SubtaskSpec,
        source: str,
        manifest: DiscoveryManifest,
        found: dict[str, int],
        saved: dict[str, int],
        db_counts: dict[str, int],
    ) -> DiscoveryVerificationRequest:
        labels = sorted(set(found) | set(saved))
        return DiscoveryVerificationRequest(
            task_prompt=subtask.description,
            discovery_script=source,
            manifest_json=json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
            target_report="\n".join(
                f"DISCOVERY target={label} found={found.get(label, 0)} saved={saved.get(label, 0)}" for label in labels
            ),
            db_inventory="\n".join(f"{label}: {count}" for label, count in sorted(db_counts.items())),
        )

    def _discovery_deps(self) -> VerificationAgentDeps:
        from browser_agent.adapters.execution.subprocess_read_script_runner import SubprocessReadScriptRunner
        from browser_agent.configuration import DISCOVERY_VERIFICATION_EXPLORE_LIMIT, ZENDRIVER_HEADLESS
        from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession

        return VerificationAgentDeps(
            browser_session=ZendriverBrowserSession(
                headless=ZENDRIVER_HEADLESS,
                user_data_dir=self._run_path / "profile_verifier",
            ),
            db_path=self._db_path,
            downloads_path=self._downloads_path,
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=0,
            explore_limit=DISCOVERY_VERIFICATION_EXPLORE_LIMIT,
            query_db_limit=_query_limit(),
            script_run_limit=_script_run_limit(),
        )

    async def _run_discovery_agent(self, request: DiscoveryVerificationRequest) -> VerificationReport:
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.use_cases.verify_discovery_use_case import VerifyDiscoveryUseCase

        model = build_llm().get_model()
        return await VerifyDiscoveryUseCase(self._discovery_deps(), model).execute(request)

    def _persist_report(self, report: VerificationReport, subtask: SubtaskSpec, state_store) -> None:
        """Write verification_report.json + ScriptToolsFeedback for one subtask."""
        from browser_agent.use_cases.verification_report_writer import VerificationReportWriter

        subtask_report_dir = self._run_path / "flow" / "subtasks" / subtask.subtask_id
        subtask_report_dir.mkdir(parents=True, exist_ok=True)
        writer = VerificationReportWriter(self._run_path, output_dir=subtask_report_dir)
        _ = writer.write(report)
        feedback = ScriptToolsFeedback(
            subtask_id=subtask.subtask_id,
            improvements=report.script_tools_improvements,
            summary=f"{len(report.script_tools_improvements)} improvements from verification",
        )
        if state_store is not None:
            state_store.write_report(subtask.subtask_id, "script_tools_feedback", feedback)

    @staticmethod
    def passed(report: VerificationReport) -> bool:
        from browser_agent.domain.probe_result import ProbeVerdict

        # Reject if there are explicit missing paths or missing count > 0
        if report.missing_count > 0 or report.missing_coverage:
            return False
        # If expected total is specified and > 0, observed must meet or exceed it
        if report.expected_pdf_total > 0 and report.observed_pdf_total < report.expected_pdf_total:
            return False
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


@dataclass
class _DiscoveryGate:
    """Outcome of the pre-LLM fast-fail checks for one discovery subtask."""

    manifest: DiscoveryManifest | None
    failure: VerificationReport | None


def _discovery_gate(source: str | None, db_count: int) -> _DiscoveryGate:
    """Fast-fail without LLM/browser when inputs are unusable or empty."""
    result = extract_manifest_detailed(source) if source is not None else None
    problems: list[str] = []
    if source is None:
        problems.append(
            "Discovery script source unreadable (state.json record missing or script deleted); "
            "re-run step 0 so the verifier can audit the real script."
        )
    if result is not None and result.error is not None:
        problems.append(f"DISCOVERY_MANIFEST invalid — fix the script's manifest literal: {result.error}")
    if db_count == 0:
        problems.append(
            "metadata.db.discovered_links has zero rows — discovery saved nothing; check the "
            "script's save path (save_discovered_link calls) before any download phase."
        )
    if problems:
        return _DiscoveryGate(None, _failed_discovery_report(problems))
    assert result is not None and result.manifest is not None  # no problems above
    return _DiscoveryGate(result.manifest, None)


def _failed_discovery_report(problems: list[str]) -> VerificationReport:
    """Failed report whose entries also block ``passed()`` (no LLM needed)."""
    return VerificationReport(
        overall_assessment=(f"Discovery could not be verified: {len(problems)} pre-agent blocker(s) detected."),
        coverage_complete=False,
        missing_coverage=[
            MissingCoverage(
                navigation_path="discovery verification preconditions",
                expected="a readable discovery script with a valid manifest and non-empty discovered_links",
                actual=problem,
                reason="pre-agent blocker",
                step_0_fix=problem,
            )
            for problem in problems
        ],
        recommendations="Fix the blockers below before the download phase runs.",
        script_tools_improvements=problems,
    )


def _find_record(state_store, subtask_id: str) -> SubtaskRecord | None:
    if state_store is None:
        return None
    state = state_store.load()
    if state is None:
        return None
    for record in state.records:
        if record.subtask_id == subtask_id:
            return record


def _deterministic_discovery_gaps(
    found: dict[str, int],
    saved: dict[str, int],
    db_counts: dict[str, int],
) -> list[MissingCoverage]:
    """DB-vs-script diff: every target where persisted rows trail the claim.

    Only compares persisted counts against the script's own reported
    found/saved figures (per plan); zero-link targets are left to the
    live agent, which can tell a genuinely empty filter from a broken one.
    """
    gaps: list[MissingCoverage] = []
    labels = sorted(set(found) | set(saved))
    for label in labels:
        expected = max(found.get(label, 0), saved.get(label, 0))
        observed = db_counts.get(label, 0)
        if observed < expected:
            gaps.append(_label_gap(label, expected, observed, "fewer links persisted than the script reported finding"))
    return gaps


def _label_gap(label: str, expected: int, observed: int, reason: str) -> MissingCoverage:
    return MissingCoverage(
        navigation_path=f"discovery target '{label}'",
        expected_total=expected,
        observed_total=observed,
        expected=f"{expected} links reported by the discovery script",
        actual=f"{observed} rows in discovered_links for filter_label='{label}'",
        reason=reason,
        step_0_fix=(
            f"Re-walk target '{label}': fix link extraction/pagination/persistence so every "
            f"found link lands in discovered_links (reported {expected}, persisted {observed})."
        ),
    )


def _merge_deterministic_gaps(report: VerificationReport, gaps: list[MissingCoverage]) -> VerificationReport:
    """Force deterministic under-collection findings into the agent's verdict."""
    if not gaps:
        return report
    improvements = list(report.script_tools_improvements)
    for gap in gaps:
        improvements.append(
            f"{gap.navigation_path}: reported={gap.expected_total} persisted={gap.observed_total} — {gap.step_0_fix}"
        )
    return report.model_copy(
        update={
            "coverage_complete": False,
            "missing_coverage": list(report.missing_coverage) + gaps,
            "script_tools_improvements": improvements,
        }
    )
