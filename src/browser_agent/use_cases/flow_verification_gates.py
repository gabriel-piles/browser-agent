"""Deterministic coverage + HTML gates shared by the flow verifier.

Extracted verbatim from the legacy
:meth:`SubtaskVerifierUseCase._verify_processing` post-agent gates so
the flow verifier reuses the same pass/fail semantics without touching
the legacy class: the discovered-links coverage gate and the
HTML-capture gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from loguru import logger

from browser_agent.domain.flow_verification_report import FlowVerificationReport
from browser_agent.domain.missing_coverage import MissingCoverage
from browser_agent.use_cases.metadata_db import count_discovered_links, parse_row_data, query_rows


def apply_processing_gates(
    report: FlowVerificationReport,
    db_path: Path,
    downloads_path: Path,
    subtask_id: str,
    require_html_files: bool,
) -> FlowVerificationReport:
    """Apply the legacy deterministic gates to a flow verification report."""
    report = _coverage_gate(report, db_path, subtask_id)
    report = _html_gate(report, db_path, downloads_path, subtask_id, require_html_files)
    return report


def _coverage_gate(report: FlowVerificationReport, db_path: Path, subtask_id: str) -> FlowVerificationReport:
    """Fail when discovered_links has rows but this subtask saved zero records."""
    try:
        disc_total = count_discovered_links(db_path)
        subtask_rows = query_rows(db_path, subtask_id)
        if disc_total > 0 and len(subtask_rows) == 0:
            report.coverage_complete = False
            report.missing_count = max(report.missing_count, 1)
            report.missing_coverage.append(
                MissingCoverage(
                    navigation_path=f"processing {subtask_id}",
                    expected=f">0 metadata records from {disc_total} discovered links",
                    actual="0 metadata records saved",
                    reason="processing script processed 0 records despite discovered links existing",
                    step_0_fix=f"Ensure subtask {subtask_id} loads and processes its discovered links without starvation.",
                )
            )
    except Exception as exc:
        logger.exception("coverage gate errored: {exc}", exc=exc)
        report.coverage_complete = False
        report.missing_coverage.append(
            MissingCoverage(
                navigation_path=f"processing {subtask_id}",
                expected="coverage gate to run",
                actual=f"coverage gate errored: {exc}",
                reason="the deterministic coverage check crashed",
                step_0_fix=f"Ensure subtask {subtask_id} saves records and that metadata.db is accessible.",
            )
        )
    return report


def _html_gate(
    report: FlowVerificationReport,
    db_path: Path,
    downloads_path: Path,
    subtask_id: str,
    require_html_files: bool,
) -> FlowVerificationReport:
    """Deterministic HTML-capture gate (no LLM), as in the legacy verifier."""
    missing = _missing_html_records(db_path, downloads_path, subtask_id, require_html_files)
    report.html_required = require_html_files
    report.html_capture_complete = not missing
    report.html_missing_records = missing
    if missing:
        report.script_tools_improvements.append(
            f"{len(missing)} record(s) downloaded a document but captured no required HTML file "
            f"(html_filename empty or missing from downloads/). Re-run the processing script with "
            f"save_page_html + html_filename per record."
        )
    return report


def _missing_html_records(
    db_path: Path,
    downloads_path: Path,
    task_slug: str,
    require_html_files: bool,
) -> list[str]:
    """Return core_ids whose downloaded document lacks the required HTML.

    Mirrors the legacy ``subtask_verifier_use_case._missing_html_records``
    gate exactly. Deterministic, no-LLM; a missing DB table yields no
    findings (empty run).
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
        if html_name and (downloads_path / html_name).is_file():
            continue
        missing.append(
            f"{core_id} (downloaded document has no captured HTML; set core_html_filename to a save_page_html result)"
        )
    return missing
