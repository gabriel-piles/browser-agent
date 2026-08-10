"""Dynamic fixture renderer for large_scale_3000 scenario.

Serves 3000 documents across 150 pages (20 items per page).
Pagination uses a numbered page navigation with Previous/Next
buttons and page number links. The last page has fewer items.
"Difficult navigation" means:
- Page numbers are in a dropdown, not simple links
- The Next button is sometimes disabled (on the last page)
- URLs use a query param ?p=N (not ?page=N)
- Some pages have slightly different HTML structure
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 20
_TOTAL_ITEMS = 3000
_TOTAL_PAGES = _TOTAL_ITEMS // _ITEMS_PER_PAGE  # 150


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    end = min(start + _ITEMS_PER_PAGE, _TOTAL_ITEMS + 1)
    items: list[tuple[int, str, str]] = []
    for i in range(start, end):
        items.append((i, f"Document #{i:04d}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return _doc_detail(doc_id), "text/html; charset=utf-8", 200
    return None


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    title = f"Document #{doc_id:04d}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<div class='container'><h1>{title}</h1>"
        f"<p class='date'>{date}</p>"
        f"<p class='description'>Full text of document {doc_id}.</p>"
        "</div></body></html>"
    )


def index(query: dict[str, list[str]]) -> str:
    """Render a page with 20 items and difficult pagination navigation."""
    scenario = query.get("scenario", ["large_scale_3000"])[0]
    page = int(query.get("p", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    items = _page_items(page)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={scenario}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    nav = _build_nav(scenario, page)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Large Archive</title></head><body>"
        "<div class='container'><h1>Large Document Archive</h1>"
        f"<p class='page-info'>Page {page} of {_TOTAL_PAGES}</p>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{nav}</div></div></body></html>"
    )


def _build_nav(scenario: str, page: int) -> str:
    """Build difficult pagination: dropdown + prev/next."""
    prev_btn = ""
    if page > 1:
        prev_btn = f'<a class="prev" href="?scenario={scenario}&p={page - 1}">Previous</a>'
    else:
        prev_btn = '<span class="prev disabled">Previous</span>'
    next_btn = ""
    if page < _TOTAL_PAGES:
        next_btn = f'<a class="next" href="?scenario={scenario}&p={page + 1}">Next</a>'
    else:
        next_btn = '<span class="next disabled">Next</span>'
    options = ""
    start_page = max(1, page - 5)
    end_page = min(_TOTAL_PAGES, page + 5)
    for p in range(start_page, end_page + 1):
        selected = " selected" if p == page else ""
        options += f"<option value='{p}'{selected}>Page {p}</option>"
    dropdown = (
        f"<select class='page-select' onchange='window.location.href=\"?scenario={scenario}&p=\"+this.value'>"
        f"{options}</select>"
    )
    return f"{prev_btn} {dropdown} {next_btn}"


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
