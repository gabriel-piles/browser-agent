"""Deterministically verify source_urls against a run's ``metadata.db``.

Post-agent pass: for each source_url, assert the scraper captured it in
the DB. Produces one :class:`ProbeResult` per URL wrapped in a
:class:`ProbeVerificationReport`.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.probe_result import ProbeResult, ProbeVerdict
from browser_agent.domain.probe_verification_report import ProbeVerificationReport
from browser_agent.use_cases.metadata_db import parse_row_data, query_rows
from browser_agent.use_cases.pdf_url_matcher import PdfUrlMatcher

_DbRow = tuple[str, str, str]


def _row_document_urls(data_json: str) -> list[str]:
    """Return the download URLs recorded in a row's data blob (deduped).

    A row's ``core_id`` PK is often a synthesized page/ref/lang key;
    the real document URL lives in ``data.core_file_url``.
    """
    data = parse_row_data(data_json)
    url = data.get("core_file_url", "") or ""
    return [url] if url else []


class ProbeCorpusVerifier:
    """Verify every source_url against the run's ``metadata.db``."""

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path

    def verify(self, source_urls: list[str]) -> ProbeVerificationReport:
        """Return one :class:`ProbeVerificationReport` for all source_urls."""
        rows = query_rows(self._db_path)
        results = [self._verify_one(url, rows) for url in source_urls]
        return ProbeVerificationReport(results=results)

    def _verify_one(self, source_url: str, rows: list[_DbRow]) -> ProbeResult:
        """Check a single source_url against all DB rows; assemble its result."""
        matched = self._match_row(source_url, rows)
        verdict = ProbeVerdict.CAPTURED if matched is not None else ProbeVerdict.MISSING_URL
        return ProbeResult(
            source_url=source_url,
            verdict=verdict,
            matched_row_core_id=matched or "",
        )

    @staticmethod
    def _matches(
        source_url: str,
        row_core_id: str,
        data_json: str,
    ) -> bool:
        """Return True when a candidate source_url counts as captured by a row.

        Satisfaction is any of: an exact URL match on the row's
        ``core_id``, an exact match on a document URL stored in the
        row's ``data`` blob (``core_file_url``), or a
        prefix match against the row ``core_id`` (a listing page).
        Only the row PK is prefix-matched — never ``core_file_url`` values.
        """
        if PdfUrlMatcher.match(source_url, row_core_id).matched:
            return True
        if any(PdfUrlMatcher.match(source_url, url).matched for url in _row_document_urls(data_json)):
            return True
        return _prefix_match(source_url, row_core_id)

    def _match_row(self, source_url: str, rows: list[_DbRow]) -> str | None:
        """Return the matched row core_id, or ``None`` when no row matches.

        A candidate counts as captured when it matches the row's
        ``core_id`` OR the document URL stored in its ``data`` blob
        (``core_file_url``) — the same URL the reconciler
        verifies against disk — or is a listing-page prefix of the row PK.
        """
        for row_source, _slug, data_json in rows:
            if self._matches(source_url, row_core_id=row_source, data_json=data_json):
                return row_source
        return None


def _prefix_match(candidate: str, stored: str) -> bool:
    """Return True when ``candidate`` (a listing page) prefixes a row's PK.

    Processing rows are saved with ``core_id`` shaped as
    ``f"{listing_url}/{document_ref}/{language}/{file_type}"`` (see the
    builder's system prompt), so a listing-page probe URL is satisfied
    when a row was captured from that page. Never prefix-match a bare
    scheme/host or a short tail (``len(cand) > 8``), and require a
    path/query boundary so ``file.doc`` cannot match ``file.docx``.
    """
    cand = PdfUrlMatcher.normalize(candidate).rstrip("/")
    sto = PdfUrlMatcher.normalize(stored)
    if not cand or len(cand) <= 8 or not sto.startswith(cand):
        return False
    rest = sto[len(cand) :]
    return rest[:1] in ("", "/", "?")
