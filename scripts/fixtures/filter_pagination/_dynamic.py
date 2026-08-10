"""Dynamic fixture renderer for filter_pagination scenario.

Two categories (reports, resolutions), each with 3 pages of 5 items
(30 total). A <select> dropdown navigates between categories via JS,
resetting to page 1 on change. Next/Previous buttons paginate within
the selected category. Detail pages at /doc/N via custom_route.
"""

from __future__ import annotations

_SCENARIO = "filter_pagination"
_CATEGORIES = ["reports", "resolutions"]
_ITEMS_PER_PAGE = 5
_PAGES_PER_CATEGORY = 3


def _category_items(category: str, page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for a category+page."""
    base = _CATEGORIES.index(category) * _ITEMS_PER_PAGE * _PAGES_PER_CATEGORY
    start = base + (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"{category.title()} #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    title = f"Document #{doc_id}"
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


def _render_nav(category: str, page: int) -> str:
    """Render prev/next navigation within a category."""
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="?scenario={_SCENARIO}&category={category}&page={page - 1}">Previous</a> '
    next_btn = ""
    if page < _PAGES_PER_CATEGORY:
        next_btn = f'<a class="next" href="?scenario={_SCENARIO}&category={category}&page={page + 1}">Next</a>'
    return f"{prev}{next_btn}"


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the selected category and page."""
    category = query.get("category", [_CATEGORIES[0]])[0]
    if category not in _CATEGORIES:
        category = _CATEGORIES[0]
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _PAGES_PER_CATEGORY))
    items = _category_items(category, page)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    options = ""
    for cat in _CATEGORIES:
        selected = " selected" if cat == category else ""
        options += f"<option value='{cat}'{selected}>{cat.title()}</option>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Filter Pagination Archive</title></head><body>"
        "<div class='container'><h1>Filter Pagination Archive</h1>"
        f"<select id='filter-category'>{options}</select>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{_render_nav(category, page)}</div></div>"
        "<script>"
        "document.getElementById('filter-category').addEventListener('change', function(e) {"
        "  window.location.href = '?scenario=" + _SCENARIO + "&category=' + e.target.value + '&page=1';"
        "});"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
