"""Dynamic fixture renderer for relative_urls scenario.

Serves 3 pages of 10 items each (30 total). The Next button href is a
relative path (page2.html) rather than a query param. Detail links are
relative (doc/N). Uses ?page=N internally but emits relative hrefs.
Detail pages at /doc/N are served via custom_route.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 3
_SCENARIO = "relative_urls"


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"Relative Doc #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    title = f"Relative Doc #{doc_id}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<div class='container'><h1>{title}</h1>"
        f"<p class='date'>{date}</p>"
        f"<p class='description'>Full text of relative document {doc_id}.</p>"
        "</div></body></html>"
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages that the index page links to."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return _doc_detail(doc_id), "text/html; charset=utf-8", 200
    return None


def _render_nav(page: int) -> str:
    """Render the prev/next navigation with relative hrefs."""
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="page{page - 1}.html?scenario={_SCENARIO}">Previous</a> '
    next_btn = f'<a class="next" href="page{page + 1}.html?scenario={_SCENARIO}">Next</a>'
    return f"{prev}{next_btn}"


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number."""
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    items = _page_items(page)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Relative URLs Archive</title></head><body>"
        f"<div class='container'><h1>Relative Archive — Page {page}</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{_render_nav(page)}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
