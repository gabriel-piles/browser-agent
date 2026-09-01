"""Build the deterministic evidence a refresh pass shows the orchestrator.

Read-only scan of a run's ``metadata.db`` mirroring the script-layer
rule-8a retry semantics (:func:`script_tools.save_record.load_failed_downloads`)
plus the ``discovered_links`` state machine: rows still
``status='discovered'`` are unprocessed work. Missing DB file or tables
yield an empty assessment — a refresh pass never raises.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from browser_agent.domain.failed_document import FailedDocument
from browser_agent.domain.new_discovered_link import NewDiscoveredLink
from browser_agent.domain.refresh_assessment import RefreshAssessment
from browser_agent.use_cases.metadata_db import parse_row_data


class RefreshAssessmentBuilder:
    """Scan a finished run for retryable download gaps and new links."""

    def __init__(self, db_path: Path, downloads_path: Path) -> None:
        self._db_path = db_path
        self._downloads_path = downloads_path

    def build(self) -> RefreshAssessment:
        """Return the assessment; never raises on missing DB or tables."""
        return RefreshAssessment(
            failed_documents=self._failed_documents(),
            new_discovered_links=self._new_discovered_links(),
        )

    def _query(self, sql: str) -> list[tuple[str, ...]]:
        if not self._db_path.exists():
            return []
        uri = f"file:{self._db_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            try:
                return conn.execute(sql).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []

    def _failed_documents(self) -> list[FailedDocument]:
        rows = self._query("SELECT core_id, core_task_slug, data FROM metadata")
        docs: list[FailedDocument] = []
        for core_id, core_task_slug, raw in rows:
            doc = self._gap(core_id, core_task_slug, parse_row_data(raw))
            if doc is not None:
                docs.append(doc)
        return docs

    def _gap(self, core_id: str, core_task_slug: str, data: dict[str, object]) -> FailedDocument | None:
        """Mirror rule-8a retry semantics; ``None`` when nothing to retry."""
        reason = self._gap_reason(data)
        if reason == "":
            return None
        return FailedDocument(
            core_id=core_id,
            file_url=str(data.get("core_file_url", "")),
            download_status=str(data.get("core_download_status", "")),
            download_error=str(data.get("core_download_error", "")),
            subtask_id=core_task_slug,
            gap_reason=reason,
        )

    def _gap_reason(self, data: dict[str, object]) -> str:
        """Return the gap reason, or ``""`` when the row needs no retry."""
        status = data.get("core_download_status", "")
        filename = str(data.get("core_pdf_filename", ""))
        if status == "no_files":
            return ""
        if status in ("failed", "load_failed") or not filename:
            return "download_failed"
        if not (self._downloads_path / filename).is_file():
            return "file_missing"
        return ""

    def _new_discovered_links(self) -> list[NewDiscoveredLink]:
        sql = "SELECT url, filter_label FROM discovered_links WHERE status='discovered'"
        return [NewDiscoveredLink(url=url, filter_label=label) for url, label in self._query(sql)]
