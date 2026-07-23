"""Shared runtime file helpers for the PDF/HTML download adapters.

The emitted vendored blocks (:mod:`emitted_snippets`) carry their own
copies of these helpers because they must be self-contained. The live
adapters import from here so there is one real implementation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def pdf_filename_for(url: str) -> str:
    """Deterministic, collision-safe on-disk filename for ``url``."""
    return f"pdf_{hashlib.sha1(url.encode()).hexdigest()[:12]}.pdf"


def html_filename_for(url: str) -> str:
    """Deterministic, collision-safe on-disk filename for a page URL."""
    return f"html_{hashlib.sha1(url.encode()).hexdigest()[:12]}.html"


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
