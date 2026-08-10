"""Dynamic fixture renderer for large_pdf scenario.

Serves a page with 3 items, each linking to a large (~5MB) PDF. Tests
that the agent can download large files without timeout or memory
issues.
"""

from __future__ import annotations

_TOTAL_ITEMS = 3


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes; static /pdf/ serving handles the large PDFs."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 3 large-PDF links."""
    scenario = query.get("scenario", ["large_pdf"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3>'
            f'<a href="/pdf/doc{i}.pdf?scenario={scenario}">Large Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Large PDF Archive</title></head><body>"
        "<div class='container'><h1>Large PDF Documents (~5MB each)</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
