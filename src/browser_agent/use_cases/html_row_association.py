"""Row-to-HTML association helpers for per-page capture verification."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_WS_RE = re.compile(r"\s+")


def row_needle(data: dict[str, Any]) -> str:
    """Whitespace-normalized document_ref used to associate a DB row with its HTML capture."""
    return _WS_RE.sub(" ", (data.get("document_ref") or "").strip())


def html_contains_record(html_path: Path, needle: str) -> bool:
    """True when needle appears in the HTML file (whitespace-collapsed, or fully space-stripped fallback)."""
    if not needle:
        return False
    try:
        hay = html_path.read_text(errors="ignore")
    except OSError:
        return False
    if _WS_RE.sub(" ", needle) in _WS_RE.sub(" ", hay):
        return True
    return needle.replace(" ", "") in hay.replace(" ", "")
