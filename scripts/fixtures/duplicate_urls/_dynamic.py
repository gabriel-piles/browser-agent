"""Dynamic fixture renderer for duplicate_urls scenario.
3 pages of 10 items each (30 total links). Items #5 and #11 both link
to /doc/5 (same URL), and item #15 also links to /doc/5, yielding 28
unique URLs. Next-button pagination. Tests source_url deduplication.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 3
_SCENARIO = "duplicate_urls"


def _page_items(page: int) -> list[tuple[int, int, str, str]]:
    """Return (item_num, doc_id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        doc_id = i
        if i in (11, 15):
            doc_id = 5
        title = f"Dup Document #{i}"
        date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        items.append((i, doc_id, title, date))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Document #{doc_id}</title></head><body>"
        f"<div class='container'><h1>Document #{doc_id}</h1>"
        f"<p class='date'>2024-01-{doc_id:02d}</p>"
        f"<p class='description'>Full text of document {doc_id}.</p>"
        "</div></body></html>"
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return _doc_detail(doc_id), "text/html; charset=utf-8", 200
    return None


def _render_nav(page: int) -> str:
    """Render prev/next navigation for the given page."""
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="?scenario={_SCENARIO}&page={page - 1}">Previous</a> '
    if page >= _TOTAL_PAGES:
        next_btn = '<a class="next disabled">Next</a>'
    else:
        next_btn = f'<a class="next" href="?scenario={_SCENARIO}&page={page + 1}">Next</a>'
    return f"{prev}{next_btn}"


def _rows_html(items: list[tuple[int, int, str, str]]) -> str:
    """Render item rows HTML from (item_num, doc_id, title, date)."""
    rows = ""
    for _item_num, doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return rows


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number."""
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    items = _page_items(page)
    rows = _rows_html(items)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Duplicate URLs Archive</title></head><body>"
        f"<div class='container'><h1>Archive — Page {page}</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{_render_nav(page)}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
