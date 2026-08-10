"""Dynamic fixture renderer for audit_discrepancy scenario.

3 categories, 5 items each (15 total). Each <option> has a
data-count="5" attribute that the DiscoveryAuditor can use as an
oracle to cross-check that all filter values were visited and
collected the expected number of items.
"""

from __future__ import annotations

_CATEGORIES = ["reports", "resolutions", "measures"]
_ITEMS_PER_CATEGORY = 5
_SCENARIO = "audit_discrepancy"


def _items_for(category: str) -> list[tuple[int, str, str]]:
    """Return (id, title, category) tuples for the given category."""
    if category not in _CATEGORIES:
        category = _CATEGORIES[0]
    base = _CATEGORIES.index(category) * _ITEMS_PER_CATEGORY
    items: list[tuple[int, str, str]] = []
    for i in range(_ITEMS_PER_CATEGORY):
        doc_id = base + i + 1
        title = f"{category.title()} Document #{doc_id}"
        items.append((doc_id, title, category))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Document #{doc_id}</title></head><body>"
        f"<div class='container'><h1>Document #{doc_id}</h1>"
        f"<p class='date'>2024-01-{doc_id:02d}</p></div></body></html>"
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


def _options_html(category: str) -> str:
    """Render select options with data-count attributes."""
    options = ""
    for cat in _CATEGORIES:
        selected = " selected" if cat == category else ""
        options += f"<option value='{cat}'{selected} data-count='{_ITEMS_PER_CATEGORY}'>{cat.title()}</option>"
    return options


def _rows_html(items: list[tuple[int, str, str]]) -> str:
    """Render item rows HTML from (id, title, category)."""
    rows = ""
    for doc_id, title, cat in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="category">{cat}</span></div>\n'
        )
    return rows


def index(query: dict[str, list[str]]) -> str:
    """Render the page with the selected filter's items."""
    category = query.get("category", [_CATEGORIES[0]])[0]
    if category not in _CATEGORIES:
        category = _CATEGORIES[0]
    items = _items_for(category)
    options = _options_html(category)
    rows = _rows_html(items)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Audit Discrepancy Archive</title></head><body>"
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
