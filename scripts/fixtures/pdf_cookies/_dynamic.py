"""Dynamic fixture renderer for pdf_cookies scenario.

Serves a page with 5 items. The listing page sets a session cookie via
JS. PDF downloads only succeed when the ?session=abc123 query param is
present; otherwise a 403 is returned. Tests that the agent propagates
session tokens (here carried as a query param) to download URLs.
"""

from __future__ import annotations

from pathlib import Path

_TOTAL_ITEMS = 5
_SESSION_TOKEN = "abc123"
_FIXTURES_DIR = Path(__file__).parent


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[bytes, str, int, dict[str, str]] | None:
    """Serve /pdf/docN.pdf only when ?session=abc123 is present."""
    if not path.startswith("/pdf/"):
        return None
    name = path.split("/")[-1]
    try:
        doc_num = int(name.replace("doc", "").replace(".pdf", ""))
    except ValueError:
        return None
    if doc_num < 1 or doc_num > _TOTAL_ITEMS:
        return None
    session = query.get("session", [""])[0]
    if session != _SESSION_TOKEN:
        return "Forbidden: session required", "text/plain; charset=utf-8", 403, {}
    pdf_path = _FIXTURES_DIR / f"doc{doc_num}.pdf"
    if not pdf_path.is_file():
        return "PDF not found", "text/plain; charset=utf-8", 404, {}
    return pdf_path.read_bytes(), "application/pdf", 200, {}


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 5 PDF links carrying the session token."""
    scenario = query.get("scenario", ["pdf_cookies"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3>'
            f'<a href="/pdf/doc{i}.pdf?scenario={scenario}&session={_SESSION_TOKEN}">'
            f"Document {i}</a></h3>"
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Cookie PDF Archive</title></head><body>"
        "<div class='container'><h1>PDF Archive (session required)</h1>"
        "<script>document.cookie = 'session_id=abc123; path=/';</script>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
