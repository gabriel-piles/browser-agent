"""Dynamic fixture renderer for dropdown_filter scenario.

Serves a page with a <select> dropdown with 4 category options.
Changing the filter (via ?category=value) shows a different set
of 5 items per category (20 total).
"""

from __future__ import annotations

_CATEGORIES = ["reports", "resolutions", "measures", "decisions"]
_ITEMS_PER_CATEGORY = 5


def _items_for(category: str) -> list[tuple[int, str, str]]:
    """Return (id, title, category) tuples for the given category."""
    base = _CATEGORIES.index(category) * _ITEMS_PER_CATEGORY if category in _CATEGORIES else 0
    items: list[tuple[int, str, str]] = []
    for i in range(_ITEMS_PER_CATEGORY):
        doc_id = base + i + 1
        title = f"{category.title()} Item #{doc_id}"
        items.append((doc_id, title, category))
    return items


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
            f'<div class="item"><h3><a href="/doc/{doc_id}?category={cat}">{title}</a></h3>'
            f'<span class="category">{cat}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Filtered Archive</title></head><body>"
        "<div class='container'><h1>Filtered Document List</h1>"
        f"<select id='filter-category'>{options}</select>"
        f"<div class='item-list'>{rows}</div></div>"
        "<script>"
        "document.getElementById('filter-category').addEventListener('change', function(e) {"
        "  window.location.href = '?scenario=dropdown_filter&category=' + e.target.value;"
        "});"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
