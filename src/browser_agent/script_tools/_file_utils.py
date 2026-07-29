"""Shared file utilities for script_tools helpers.

Moved verbatim from ``browser_agent.adapters.emitted_snippets`` — atomic
writes, PDF magic-byte checks, existence checks, deterministic filename
derivation. Stdlib-only so every script that imports any script_tools
helper works without extra dependencies.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit, unquote, quote

import hashlib
import os as _os
from pathlib import Path


def _write_atomic(path, data):
    """Write ``data`` to ``path`` atomically (temp + rename). On any failure,
    remove the temp file. Renames are atomic on POSIX so a crash mid-write
    never leaves a partial file at ``path``. ``path`` may be ``str`` or ``Path``."""
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(part, path)
    except Exception:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise


def _assert_pdf_magic(path, data, url):
    """Delete ``path`` and raise if ``data`` is not a real PDF.

    Checks the first 4 bytes are b"%PDF" and the last 1024 bytes
    contain b"%%EOF". On failure the file is removed (it was just
    written by _write_atomic) and RuntimeError is raised so the
    caller records a failed row instead of persisting a corrupt one.
    """
    if not (data[:4] == b"%PDF" and b"%%EOF" in data[-1024:]):
        try:
            Path(path).unlink()
        except OSError:
            pass
        raise RuntimeError(f"non-PDF body for {url} (first 4 bytes: {data[:4]!r})")


def _existing_size(path):
    """Return existing on-disk size in bytes, or 0 when missing/empty/corrupt."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    return st.st_size if st.st_size > 0 else 0


def _canonical_url(url):
    """Canonical form of ``url`` for dedup keys (filename hash, DB PK).

    Collapses percent-encoded and raw-unicode path forms of the same
    document onto one key: unquotes then re-quotes the path so
    ``bel%C3%A9m-do-par%C3%A1`` and ``belém-do-pará`` both become
    ``bel%C3%A9m-do-par%C3%A1``. Also lowercases scheme+host and
    upgrades http -> https (mirrors the old _normalize_scheme). The
    query is kept as-is (NOT re-encoded — query params are already
    percent-encoded by the browser and re-encoding would corrupt
    literal '+'). Fragment dropped. Non-str input passes through
    unchanged so the function is safe to call on untyped values.
    """
    if not isinstance(url, str) or not url:
        return url
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme.lower() in {"http", "https"} else parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = quote(unquote(parts.path), safe="/%@") if parts.path else ""
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def pdf_id_for(url):
    """``pdf_<sha1(canonical_url)[:12]>`` — the download helper's id stem.

    Use this at discovery time (before any download) so the DB
    ``source_url`` key, the stored ``pdf_id``, and the on-disk filename
    stem all derive from the SAME canonical URL. NEVER inline
    ``hashlib.sha1(pdf_url.encode())`` — it skips percent-encoding
    normalization and can create a duplicate row for the same PDF.
    """
    return f"pdf_{hashlib.sha1(_canonical_url(url).encode()).hexdigest()[:12]}"


def _pdf_filename_for(url):
    """Deterministic, collision-safe on-disk filename for ``url``.

    Returns ``<pdf_id_for(url)>.pdf`` — a pure function of the
    canonical (percent-normalized) download URL, so "file exists at
    path" == "this exact PDF was already downloaded" regardless of
    page order, label reuse, or percent-encoded vs raw-unicode form.
    """
    return f"{pdf_id_for(url)}.pdf"


def _html_filename_for(url):
    """Deterministic, collision-safe on-disk filename for ``url``.

    Returns ``html_<sha1(canonical_url)[:12]>.html`` — a pure function
    of the canonical (percent-normalized) source URL, so "file exists
    at path" == "this exact page HTML was already saved" regardless of
    page order or percent-encoded vs raw-unicode form. Mirrors the PDF
    naming scheme so the two never collide (different prefix + ext).
    """
    return f"html_{hashlib.sha1(_canonical_url(url).encode()).hexdigest()[:12]}.html"
