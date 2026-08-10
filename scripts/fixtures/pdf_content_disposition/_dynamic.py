"""Dynamic fixture renderer for pdf_content_disposition scenario.

Serves a page with 5 items, each linking to a PDF that the server
returns with a Content-Disposition: attachment header using a custom
filename. Tests that the agent respects Content-Disposition filenames.
"""

from __future__ import annotations

from pathlib import Path

_TOTAL_ITEMS = 5
_FIXTURES_DIR = Path(__file__).parent


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[bytes, str, int, dict[str, str]] | None:
    """Handle /pdf/ routes with a Content-Disposition attachment header."""
    if not path.startswith("/pdf/"):
        return None
    name = path.split("/")[-1]
    try:
        doc_num = int(name.replace("doc", "").replace(".pdf", ""))
    except ValueError:
        return None
    if doc_num < 1 or doc_num > _TOTAL_ITEMS:
        return None
    pdf_path = _FIXTURES_DIR / f"doc{doc_num}.pdf"
    if not pdf_path.is_file():
        return "PDF not found", "text/plain; charset=utf-8", 404, {}
    body = pdf_path.read_bytes()
    headers = {"Content-Disposition": f'attachment; filename="custom_doc{doc_num}.pdf"'}
    return body, "application/pdf", 200, headers


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 5 PDF links served with Content-Disposition."""
    scenario = query.get("scenario", ["pdf_content_disposition"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3>'
            f'<a href="/pdf/doc{i}.pdf?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Content-Disposition Archive</title></head><body>"
        "<div class='container'><h1>PDF Archive (attachment download)</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
