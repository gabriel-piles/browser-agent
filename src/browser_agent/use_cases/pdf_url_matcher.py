"""URL normalization and matching shared by the reconciler and ``check_pdf``.

Keeps the two paths from drifting: both the deterministic reconciler
and the LLM ``check_pdf`` tool call into :class:`PdfUrlMatcher`, so a
candidate recorded under a different URL form is reconciled to the
right row instead of being reported as a false ``missing_from_db``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit, unquote, quote


def expected_pdf_filename(pdf_url: str) -> str:
    """Return ``pdf_<sha1(url)[:12]>.pdf``, the downloader's naming scheme.

    Mirrors ``_pdf_filename_for`` in ``script_tools/_file_utils.py``: the
    on-disk name is a pure function of the canonical download URL.
    Routes through :meth:`PdfUrlMatcher.normalize` so the
    percent-encoded and raw-unicode forms of the same path hash to the
    same filename. The downloader upgrades ``http://`` to ``https://``
    *before* hashing, so callers MUST pass the normalized (``https``)
    form.
    """
    return f"pdf_{hashlib.sha1(PdfUrlMatcher.normalize(pdf_url).encode()).hexdigest()[:12]}.pdf"


@dataclass(frozen=True)
class UrlMatch:
    """Outcome of matching a candidate URL against a stored URL."""

    matched: bool
    mode: str
    stored_url: str = ""


class PdfUrlMatcher:
    """Normalize URLs both ways and match with a transparent mode label."""

    @staticmethod
    def normalize(url: str) -> str:
        """Return the canonical form used for comparison.

        Lowercases scheme+host, upgrades ``http`` to ``https`` (mirrors the
        downloader), percent-canonicalizes the path (unquote then
        re-quote so encoded and raw-unicode forms collapse), drops the
        fragment, and sorts query params.
        """
        if not url:
            return ""
        parts = urlsplit(url.strip())
        scheme = "https" if parts.scheme in {"http", "https"} else parts.scheme.lower()
        netloc = parts.netloc.lower()
        query = _sorted_query(parts.query)
        path = quote(unquote(parts.path), safe="/%@") if parts.path else ""
        return urlunsplit((scheme, netloc, path, query, ""))

    @staticmethod
    def match(candidate: str, stored: str) -> UrlMatch:
        """Return how ``candidate`` matched ``stored`` (or that it did not)."""
        norm_c = PdfUrlMatcher.normalize(candidate)
        norm_s = PdfUrlMatcher.normalize(stored)
        if norm_c and norm_c == norm_s:
            return UrlMatch(True, "normalized", stored)
        if _suffix_match(candidate, stored):
            return UrlMatch(True, "suffix", stored)
        return UrlMatch(False, "none")

    @staticmethod
    def expected_filenames_for(pdf_url: str) -> tuple[str, str]:
        """Return ``(normalized_name, original_name)`` to try both on-disk forms.

        A row stored with the original ``http://`` URL hashes to a name
        that is not on disk; the downloader hashed the upgraded ``https``
        form. Trying both catches that false negative. ``normalized_name``
        is the one the downloader would have produced; ``original_name``
        is what a naive hash of the stored URL gives.
        """
        normalized = PdfUrlMatcher.normalize(pdf_url)
        return expected_pdf_filename(normalized), expected_pdf_filename(pdf_url)


def _sorted_query(query: str) -> str:
    if not query:
        return ""
    pairs = sorted(pair.split("=", 1) for pair in query.split("&") if pair)
    return "&".join("=".join(p) for p in pairs)


def _suffix_match(candidate: str, stored: str) -> bool:
    if not candidate or not stored:
        return False
    cand = candidate.rstrip("/").lower()
    sto = stored.rstrip("/").lower()
    if not cand or not sto:
        return False
    if len(cand) > len(sto):
        cand, sto = sto, cand
    return sto.endswith(cand) and len(cand) > 8
