"""Dynamic fixture renderer for pdf_timeout scenario.

Serves 5 items each linking to a PDF. PDFs 1-3 download normally,
PDFs 4-5 cause the server to hang (status 998 sentinel) — the
fixture server never responds. Tests that the agent's download
logic handles hung requests without blocking forever.
"""

from __future__ import annotations

_TOTAL_ITEMS = 5
_HANG_THRESHOLD = 3


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Hang on PDFs 4 and 5; fall through for PDFs 1-3."""
    if path.startswith("/pdf/"):
        name = path.split("/")[-1]
        try:
            doc_num = int(name.replace("doc", "").replace(".pdf", ""))
        except ValueError:
            return None
        if doc_num > _HANG_THRESHOLD:
            return "", "application/pdf", 998
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 5 items linking to PDFs."""
    scenario = query.get("scenario", ["pdf_timeout"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3><a href="/pdf/doc{i}.pdf?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>PDF Timeout Archive</title></head><body>"
        "<div class='container'><h1>PDF Timeout Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
