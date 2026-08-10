"""Dynamic fixture renderer for concurrency scenario.

Serves a page with 50 items. Each item links to a detail page
that takes ~100ms to "load" (simulated work). Designed to
exercise parallel_runners=4.
"""

from __future__ import annotations

_TOTAL_ITEMS = 50


def index(query: dict[str, list[str]]) -> str:
    """Render the page with all 50 items."""
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        title = f"Concurrent Document #{i}"
        date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario=concurrency">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Concurrency Archive</title></head><body>"
        "<div class='container'><h1>Concurrency Archive — 50 Items</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
