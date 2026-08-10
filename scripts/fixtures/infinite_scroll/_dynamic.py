"""Dynamic fixture renderer for infinite_scroll scenario.

Serves an initial page with 10 items and a "Load more" button.
AJAX requests to /fragment/?page=N return HTML fragments with
10 more items. After 3 pages (30 items total) the button
disappears.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 3


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"Scroll Item #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def index(query: dict[str, list[str]]) -> str:
    """Render the initial page with first 10 items + Load more button."""
    items = _page_items(1)
    rows = ""
    for doc_id, title, date in items:
        rows += f'<div class="item"><h3><a href="/doc/{doc_id}">{title}</a></h3><span class="date">{date}</span></div>\n'
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Infinite Scroll Archive</title></head><body>"
        "<div class='container'><h1>Scroll Archive</h1>"
        f"<div class='item-list' id='item-list'>{rows}</div>"
        "<button class='load-more' id='load-more' data-page='2'>Load more</button>"
        "</div>"
        "<script>"
        "document.getElementById('load-more').addEventListener('click', async function() {"
        "  var btn = this; var page = parseInt(btn.dataset.page);"
        "  var resp = await fetch('/fragment/?scenario=infinite_scroll&page=' + page);"
        "  var html = await resp.text();"
        "  document.getElementById('item-list').insertAdjacentHTML('beforeend', html);"
        "  if (page >= 3) { btn.remove(); } else { btn.dataset.page = page + 1; }"
        "});"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Return an HTML fragment for the requested page."""
    page = int(query.get("page", ["2"])[0])
    if page < 1 or page > _TOTAL_PAGES:
        return ""
    items = _page_items(page)
    rows = ""
    for doc_id, title, date in items:
        rows += f'<div class="item"><h3><a href="/doc/{doc_id}">{title}</a></h3><span class="date">{date}</span></div>\n'
    return rows
