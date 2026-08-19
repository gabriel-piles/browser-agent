"""Deterministically verify source_urls against a run's ``metadata.db``.

Post-agent pass: for each source_url, assert the scraper captured it in
the DB. Produces one :class:`ProbeResult` per URL wrapped in a
:class:`ProbeVerificationReport`.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.probe_result import ProbeResult, ProbeVerdict
from browser_agent.domain.probe_verification_report import ProbeVerificationReport
from browser_agent.use_cases.metadata_db import query_rows
from browser_agent.use_cases.pdf_url_matcher import PdfUrlMatcher

_DbRow = tuple[str, str, str]


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
            matched_row_source_url=matched or "",
        )

    def _match_row(self, source_url: str, rows: list[_DbRow]) -> str | None:
        """Return the matched row source_url, or ``None`` when no row matches."""
        for row_source_url, _slug, _data_json in rows:
            if PdfUrlMatcher.match(source_url, row_source_url).matched:
                return row_source_url
        return None
