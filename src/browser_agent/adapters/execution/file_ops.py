"""Shared runtime file helpers for the PDF/HTML download adapters.

The ``script_tools._file_utils`` module carries its own copies of
these helpers because emitted scripts must be standalone. The live
adapters import from here so there is one real implementation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, unquote, quote


def _canonical_url(url):
    """Canonical form of ``url`` for dedup keys (filename hash, DB PK).

    Mirrors ``script_tools._file_utils._canonical_url``: the file_ops
    module is a deliberate separate copy (emitted scripts must be
    standalone). Unquotes then re-quotes the path so the
    percent-encoded and raw-unicode forms of the same document collapse
    to one key. Lowercases scheme+host, upgrades http -> https, keeps
    the query as-is, drops the fragment. Non-str passes through.
    """
    if not isinstance(url, str) or not url:
        return url
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme.lower() in {"http", "https"} else parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = quote(unquote(parts.path), safe="/%@") if parts.path else ""
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def pdf_id_for(url: str) -> str:
    """``pdf_<sha1(canonical_url)[:12]>`` — the downloader's id stem."""
    return f"pdf_{hashlib.sha1(_canonical_url(url).encode()).hexdigest()[:12]}"


def pdf_filename_for(url: str) -> str:
    """Deterministic, collision-safe on-disk filename for ``url``."""
    return f"{pdf_id_for(url)}.pdf"


def html_filename_for(url: str) -> str:
    """Deterministic, collision-safe on-disk filename for a page URL."""
    return f"html_{hashlib.sha1(_canonical_url(url).encode()).hexdigest()[:12]}.html"


def existing_size(path: Path) -> int:
    """Return existing on-disk size in bytes, or 0 when missing/empty."""
    try:
        st = path.stat()
    except (FileNotFoundError, OSError):
        return 0
    return st.st_size if st.st_size > 0 else 0


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp + rename)."""
    import os as _os

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


def assert_pdf_magic(path: Path, data: bytes, url: str) -> None:
    """Delete ``path`` and raise RuntimeError if ``data`` is not a real PDF."""
    if not (data[:4] == b"%PDF" and b"%%EOF" in data[-1024:]):
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError(f"non-PDF body for {url} (first 4 bytes: {data[:4]!r})")
