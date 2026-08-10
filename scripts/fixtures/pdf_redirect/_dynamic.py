"""Dynamic fixture renderer for pdf_redirect scenario.

Serves a page with 5 items. Each /pdf/docN.pdf link 301-redirects to
/file/docN.pdf. Tests that the agent follows redirects when fetching
PDF documents.
"""

from __future__ import annotations

_TOTAL_ITEMS = 5


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int, dict[str, str]] | None:
    """Redirect /pdf/docN.pdf to /file/docN.pdf via 301."""
    if not path.startswith("/pdf/"):
        return None
    name = path.split("/")[-1]
    try:
        doc_num = int(name.replace("doc", "").replace(".pdf", ""))
    except ValueError:
        return None
    if doc_num < 1 or doc_num > _TOTAL_ITEMS:
        return None
    scenario = query.get("scenario", ["pdf_redirect"])[0]
    location = f"/file/doc{doc_num}.pdf?scenario={scenario}"
    return "", "text/html", 301, {"Location": location}


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 5 PDF links that redirect to /file/."""
    scenario = query.get("scenario", ["pdf_redirect"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3>'
            f'<a href="/pdf/doc{i}.pdf?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Redirect PDF Archive</title></head><body>"
        "<div class='container'><h1>PDF Archive (redirects)</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
