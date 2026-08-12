"""Dynamic fixture renderer for filter_empty scenario.

4 categories (reports/resolutions/measures/archived). "archived"
returns 0 items with a "No items found" message. Others return 5
items each (15 total). One <select> dropdown navigates via ?category=.
"""

from __future__ import annotations

_CATEGORIES = ["reports", "resolutions", "measures", "archived"]
_ITEMS_PER_CATEGORY = 5
_SCENARIO = "filter_empty"


def _items_for(category: str) -> list[tuple[int, str, str]]:
    """Return (id, title, category) tuples for the given category."""
    if category == "archived":
        return []
    base = _CATEGORIES.index(category) * _ITEMS_PER_CATEGORY
    items: list[tuple[int, str, str]] = []
    for i in range(_ITEMS_PER_CATEGORY):
        doc_id = base + i + 1
        title = f"{category.title()} Item #{doc_id}"
        items.append((doc_id, title, category))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page with title and category for ``doc_id``."""
    category = "reports"
    for cat in _CATEGORIES:
        base = _CATEGORIES.index(cat) * _ITEMS_PER_CATEGORY
        if base < doc_id <= base + _ITEMS_PER_CATEGORY:
            category = cat
            break
    title = f"{category.title()} Item #{doc_id}"
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{title}</title></head><body>"
        f'<div class="container">'
        f'<h1 class="title">{title}</h1>'
        f'<p class="category">{category}</p>'
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


def index(query: dict[str, list[str]]) -> str:
    """Render the page with the selected filter's items."""
    category = query.get("category", [_CATEGORIES[0]])[0]
    if category not in _CATEGORIES:
        category = _CATEGORIES[0]
    items = _items_for(category)
    options = ""
    for cat in _CATEGORIES:
        selected = " selected" if cat == category else ""
        options += f"<option value='{cat}'{selected}>{cat.title()}</option>"
    rows = ""
    for doc_id, title, cat in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="category">{cat}</span></div>\n'
        )
    if not items:
        rows = '<div class="empty">No items found</div>'
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Filter Empty Archive</title></head><body>"
        "<div class='container'><h1>Filtered Document List</h1>"
        f"<select id='filter-category'>{options}</select>"
        f"<div class='item-list'>{rows}</div></div>"
        "<script>"
        "document.getElementById('filter-category').addEventListener('change', function(e) {"
        "  window.location.href = '?scenario=" + _SCENARIO + "&category=' + e.target.value;"
        "});"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
