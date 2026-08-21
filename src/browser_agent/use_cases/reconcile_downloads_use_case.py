"""Deterministic DB-vs-disk reconciler — no LLM, always runs.

For every row in ``metadata.db``: recompute the expected on-disk
filename from ``file_url``, stat the file, validate it, and diff both
directions. Also reports orphan files, ``.part`` leftovers, duplicate
``file_url`` rows, empty ``file_url`` rows, and identical-size clusters.

The output is written to disk *before* the agent runs so a model
failure mid-run still leaves usable evidence. The LLM stage keeps only
the work a model is needed for: re-walking the site for PDFs that were
never *discovered* (invisible to any DB-vs-disk diff) and root-causing
gaps against the script source.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from browser_agent.adapters.execution.file_ops import file_ext_for
from browser_agent.domain.corpus_finding import CorpusFinding
from browser_agent.domain.reconciled_pdf import ReconciledPdf
from browser_agent.use_cases.metadata_db import parse_row_data
from browser_agent.use_cases.pdf_integrity_validator import (
    PdfIntegrityResult,
    PdfIntegrityValidator,
    find_identical_size_clusters,
)
from browser_agent.use_cases.pdf_url_matcher import PdfUrlMatcher

_PART_SUFFIX = ".part"
_PDF_SUFFIX = ".pdf"
_MAX_FINDING_ITEMS = 20


class ReconcileDownloadsUseCase:
    """Build the exhaustive N-row inventory for one run directory."""

    def __init__(self, db_path: Path, downloads_path: Path, task_slug: str | None = None) -> None:
        self._db_path = db_path
        self._downloads_path = downloads_path
        self._task_slug = task_slug

    def reconcile(self) -> tuple[list[ReconciledPdf], list[CorpusFinding]]:
        """Return ``(per_row_inventory, corpus_findings)`` for the whole run."""
        try:
            rows = self._read_rows()
        except sqlite3.OperationalError:
            # Missing DB = zero recorded rows (script saved nothing). Treat
            # that as an empty inventory — a coverage gap for the verifier,
            # not a crash — matching _discovered_unprocessed_findings.
            rows = []
        disk_files = self._disk_pdf_basenames()
        per_row = [self._reconcile_row(row, disk_files) for row in rows]
        findings = self._corpus_findings(rows, disk_files, per_row)
        findings.extend(self._discovered_unprocessed_findings(rows))
        return per_row, findings

    def _read_rows(self) -> list[tuple[str, str, str]]:
        uri = f"file:{self._db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            if self._task_slug is not None:
                return conn.execute(
                    "SELECT source_url, task_slug, data FROM metadata WHERE task_slug = ?",
                    (self._task_slug,),
                ).fetchall()
            return conn.execute(
                "SELECT source_url, task_slug, data FROM metadata",
            ).fetchall()
        finally:
            conn.close()

    def _discovered_unprocessed_findings(self, rows: list[tuple[str, str, str]]) -> list[CorpusFinding]:
        """Diff ``discovered_links`` (status='discovered') vs ``metadata`` rows.

        A discovered URL is "handled" if a metadata.source_url equals it
        or starts with ``<url>/pdf/`` (the per-PDF keying convention).
        Unhandled → discovered_unprocessed; handled but still
        status='discovered' → stale_link_status. Absent table → no findings.
        """
        handled = {src for src, _, _ in rows}
        try:
            discovered = self._read_discovered()
        except sqlite3.OperationalError:
            return []
        unprocessed: list[str] = []
        stale: list[str] = []
        for url, _label in discovered:
            (stale if self._is_handled_url(url, handled) else unprocessed).append(url)
        return self._link_finding_kinds(unprocessed, stale)

    def _read_discovered(self) -> list[tuple[str, str]]:
        """Return ``[(url, filter_label)]`` for discovered_links rows still pending."""
        uri = f"file:{self._db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return conn.execute(
                "SELECT url, filter_label FROM discovered_links WHERE status='discovered'",
            ).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _is_handled_url(url: str, handled: set[str]) -> bool:
        """True if ``url`` has a matching metadata.source_url (exact or /pdf/ prefix)."""
        if url in handled:
            return True
        prefix = url + "/pdf/"
        return any(h.startswith(prefix) for h in handled)

    @staticmethod
    def _link_finding_kinds(unprocessed: list[str], stale: list[str]) -> list[CorpusFinding]:
        """Build CorpusFindings for the two discovered-link gap kinds."""
        out: list[CorpusFinding] = []
        if unprocessed:
            out.append(
                CorpusFinding(
                    kind="discovered_unprocessed",
                    detail=f"{len(unprocessed)} links in discovered_links with status='discovered' and no matching metadata row (discovery found them, processing never handled them).",
                    items=unprocessed[:_MAX_FINDING_ITEMS],
                )
            )
        if stale:
            out.append(
                CorpusFinding(
                    kind="stale_link_status",
                    detail=f"{len(stale)} links still status='discovered' but have a matching metadata row (processing handled them but mark_link_processed was not called).",
                    items=stale[:_MAX_FINDING_ITEMS],
                )
            )
        return out

    def _disk_pdf_basenames(self) -> set[str]:
        if not self._downloads_path.is_dir():
            return set()
        return {p.name for p in self._downloads_path.iterdir() if p.is_file()}

    def _reconcile_row(self, row: tuple[str, str, str], disk_files: set[str]) -> ReconciledPdf:
        source_url, _slug, data_json = row
        data = parse_row_data(data_json)
        download_status = data.get("core_download_status", "") or ""
        file_url = data.get("core_file_url", "") or ""
        if not file_url:
            return ReconciledPdf(
                source_url=source_url,
                verdict="empty_pdf_url",
                notes="row has no core_file_url",
                download_status=download_status,
            )
        return self._check_file_for_url(source_url, file_url, data, disk_files, download_status)

    def _check_file_for_url(
        self,
        source_url: str,
        file_url: str,
        data: dict[str, Any],
        disk_files: set[str],
        download_status: str,
    ) -> ReconciledPdf:
        db_filename = data.get("core_pdf_filename", "") or ""
        expected_norm, expected_orig = PdfUrlMatcher.expected_filenames_for(file_url)
        matched, mode = self._match_on_disk(expected_norm, expected_orig, disk_files)
        filename_mismatch = bool(db_filename) and db_filename != expected_norm
        if matched is None:
            return ReconciledPdf(
                source_url=source_url,
                file_url=file_url,
                db_pdf_filename=db_filename,
                expected_filename=expected_norm,
                matched_filename="",
                match_mode=mode,
                file_exists=False,
                filename_mismatch=filename_mismatch,
                verdict="file_not_downloaded",
                notes=f"expected {expected_norm} (also tried {expected_orig}); not on disk",
                download_status=download_status,
            )
        return self._validate_matched(
            source_url,
            file_url,
            db_filename,
            expected_norm,
            matched,
            mode,
            filename_mismatch,
            download_status,
        )

    def _match_on_disk(
        self,
        norm: str,
        orig: str,
        disk_files: set[str],
    ) -> tuple[Path | None, str]:
        norm_path = self._downloads_path / norm
        if norm in disk_files:
            return norm_path, "normalized"
        if orig and orig != norm and orig in disk_files:
            return self._downloads_path / orig, "original"
        return None, "missing"

    def _validate_matched(
        self,
        source_url: str,
        file_url: str,
        db_filename: str,
        expected: str,
        matched: Path,
        mode: str,
        filename_mismatch: bool,
        download_status: str,
    ) -> ReconciledPdf:
        is_document = bool(file_ext_for(file_url))
        integrity = (
            PdfIntegrityValidator.validate_document(matched) if is_document else PdfIntegrityValidator.validate(matched)
        )
        verdict = self._row_verdict(integrity)
        return ReconciledPdf(
            source_url=source_url,
            file_url=file_url,
            db_pdf_filename=db_filename,
            expected_filename=expected,
            matched_filename=matched.name,
            match_mode=mode,
            file_exists=True,
            file_size_bytes=integrity.file_size,
            is_valid_pdf=integrity.is_valid,
            is_suspiciously_small=integrity.is_suspiciously_small,
            filename_mismatch=filename_mismatch,
            verdict=verdict,
            notes=integrity.notes,
            download_status=download_status,
        )

    @staticmethod
    def _row_verdict(integrity: PdfIntegrityResult) -> str:
        if not integrity.is_valid:
            return "corrupt_file"
        if integrity.is_suspiciously_small:
            return "suspiciously_small"
        return "present"

    def _corpus_findings(
        self,
        rows: list[tuple[str, str, str]],
        disk_files: set[str],
        per_row: list[ReconciledPdf],
    ) -> list[CorpusFinding]:
        findings: list[CorpusFinding] = []
        findings.extend(self._url_findings(rows))
        findings.extend(self._disk_findings(disk_files, per_row))
        findings.extend(self._size_cluster_findings(per_row))
        return findings

    def _url_findings(self, rows: list[tuple[str, str, str]]) -> list[CorpusFinding]:
        urls: list[str] = []
        for _src, _slug, data_json in rows:
            url = parse_row_data(data_json).get("core_file_url", "") or ""
            if url:
                urls.append(url)
        counts = Counter(urls)
        out: list[CorpusFinding] = []
        dups = [u for u, c in counts.items() if c > 1]
        if dups:
            out.append(
                CorpusFinding(
                    kind="duplicate_pdf_url",
                    detail=f"{len(dups)} core_file_url value(s) appear in more than one row.",
                    items=dups[:_MAX_FINDING_ITEMS],
                )
            )
        return out

    def _disk_findings(self, disk_files: set[str], per_row: list[ReconciledPdf]) -> list[CorpusFinding]:
        out: list[CorpusFinding] = []
        claimed = {r.matched_filename for r in per_row if r.matched_filename}
        orphans = sorted(f for f in disk_files if f not in claimed and f.endswith(_PDF_SUFFIX))
        if orphans:
            out.append(
                CorpusFinding(
                    kind="orphan_file",
                    detail=f"{len(orphans)} PDF(s) on disk with no DB row.",
                    items=orphans[:_MAX_FINDING_ITEMS],
                )
            )
        parts = sorted(f for f in disk_files if f.endswith(_PART_SUFFIX))
        if parts:
            out.append(
                CorpusFinding(
                    kind="part_leftover",
                    detail=f"{len(parts)} .part file(s) — evidence of a crashed mid-download.",
                    items=parts[:_MAX_FINDING_ITEMS],
                )
            )
        return out

    def _size_cluster_findings(self, per_row: list[ReconciledPdf]) -> list[CorpusFinding]:
        sizes = {self._downloads_path / r.matched_filename: r.file_size_bytes for r in per_row if r.matched_filename}
        clusters = find_identical_size_clusters(sizes)
        out: list[CorpusFinding] = []
        for cluster in clusters:
            names = sorted(p.name for p in cluster)
            out.append(
                CorpusFinding(
                    kind="identical_size_cluster",
                    detail=f"{len(names)} files share the exact same byte size — possible repeated error page.",
                    items=names[:_MAX_FINDING_ITEMS],
                )
            )
        return out
