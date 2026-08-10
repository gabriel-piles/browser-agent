"""Dynamic fixture renderer for missing_pdfs scenario.

Serves a page with 10 items, each linking to a PDF. Half the PDFs
exist (doc1-5.pdf), the other half (doc6-10.pdf) return 404. Tests
that the agent handles 404 PDF downloads gracefully — save_record
with download_status="failed" for missing PDFs.
"""

from __future__ import annotations

_EXISTING_PDFS = 5
_TOTAL_ITEMS = 10


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /pdf/ routes: return 404 for non-existent PDFs."""
    if path.startswith("/pdf/"):
        name = path.split("/")[-1]
        try:
            doc_num = int(name.replace("doc", "").replace(".pdf", ""))
        except ValueError:
            return None
        if doc_num > _EXISTING_PDFS:
            return "PDF not found", "text/plain; charset=utf-8", 404
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 10 items, some with valid PDFs, some with broken links."""
    scenario = query.get("scenario", ["missing_pdfs"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        status = "available" if i <= _EXISTING_PDFS else "missing"
        rows += (
            f'<div class="item"><h3><a href="/pdf/doc{i}.pdf?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span>'
            f'<span class="status">{status}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Missing PDFs Archive</title></head><body>"
        "<div class='container'><h1>Document Archive (some PDFs missing)</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
