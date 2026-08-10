"""Dynamic fixture renderer for scroll_load_no_button scenario.

Serves 30 items total across 3 pages. The first page renders 10 items
with an IntersectionObserver sentinel that fetches /fragment/?page=N and
appends items on scroll. No "Load more" button is present.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 3
_SCENARIO = "scroll_load_no_button"


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"Scroll Item #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def _rows_html(items: list[tuple[int, str, str]]) -> str:
    """Render item rows HTML for a list of (id, title, date)."""
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return rows


def index(query: dict[str, list[str]]) -> str:
    """Render the initial page with first 10 items + scroll sentinel."""
    rows = _rows_html(_page_items(1))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Scroll Load Archive</title></head><body>"
        "<div class='container'><h1>Scroll Load Archive</h1>"
        f"<div class='item-list' id='item-list'>{rows}</div>"
        "<div id='sentinel' style='height:1px'></div>"
        "</div>"
        "<script>"
        "var page = 2;"
        "var sentinel = document.getElementById('sentinel');"
        "var observer = new IntersectionObserver(async function(entries) {"
        "  if (!entries[0].isIntersecting) return;"
        "  if (page > 3) { observer.disconnect(); return; }"
        "  var resp = await fetch('/fragment/?scenario=" + _SCENARIO + "&page=' + page);"
        "  var html = await resp.text();"
        "  document.getElementById('item-list').insertAdjacentHTML('beforeend', html);"
        "  page += 1;"
        "  if (page > 3) { observer.disconnect(); }"
        "});"
        "observer.observe(sentinel);"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Return an HTML fragment for pages 2 and 3."""
    page = int(query.get("page", ["2"])[0])
    if page < 2 or page > _TOTAL_PAGES:
        return ""
    return _rows_html(_page_items(page))
