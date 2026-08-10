"""Dynamic fixture renderer for disabled_next scenario.

Serves 5 pages of 10 items each (50 total). The Next button is present
on every page, but on the last page it has class="next disabled" and no
href. Detail pages at /doc/N are served via custom_route.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 5
_SCENARIO = "disabled_next"


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"Document Report #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    title = f"Document Report #{doc_id}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<div class='container'><h1>{title}</h1>"
        f"<p class='date'>{date}</p>"
        f"<p class='description'>Full text of document {doc_id}.</p>"
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
    """Render the prev/next navigation for the given page."""
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="?scenario={_SCENARIO}&page={page - 1}">Previous</a> '
    if page >= _TOTAL_PAGES:
        next_btn = '<a class="next disabled">Next</a>'
    else:
        next_btn = f'<a class="next" href="?scenario={_SCENARIO}&page={page + 1}">Next</a>'
    return f"{prev}{next_btn}"


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number."""
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    items = _page_items(page)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Disabled Next Archive</title></head><body>"
        f"<div class='container'><h1>Archive — Page {page}</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{_render_nav(page)}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
