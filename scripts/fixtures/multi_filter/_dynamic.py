"""Dynamic fixture renderer for multi_filter scenario.

Two <select> dropdowns: category (reports/resolutions/measures) and
year (2022/2023/2024). 9 combinations, 5 items each (45 total). Each
item shows its category and year. Selection navigates via query params.
"""

from __future__ import annotations

_CATEGORIES = ["reports", "resolutions", "measures"]
_YEARS = ["2022", "2023", "2024"]
_ITEMS_PER_COMBO = 5
_SCENARIO = "multi_filter"


def _items_for(category: str, year: str) -> list[tuple[int, str, str, str]]:
    """Return (id, title, category, year) tuples for the combination."""
    cat_idx = _CATEGORIES.index(category) if category in _CATEGORIES else 0
    yr_idx = _YEARS.index(year) if year in _YEARS else 0
    base = (cat_idx * len(_YEARS) + yr_idx) * _ITEMS_PER_COMBO
    items: list[tuple[int, str, str, str]] = []
    for i in range(_ITEMS_PER_COMBO):
        doc_id = base + i + 1
        title = f"{category.title()} {year} Item #{doc_id}"
        items.append((doc_id, title, category, year))
    return items


def _options(values: list[str], selected: str) -> str:
    """Render <option> tags for a list of values."""
    opts = ""
    for v in values:
        sel = " selected" if v == selected else ""
        opts += f"<option value='{v}'{sel}>{v}</option>"
    return opts


def index(query: dict[str, list[str]]) -> str:
    """Render the page with the selected filter combination."""
    category = query.get("category", [_CATEGORIES[0]])[0]
    if category not in _CATEGORIES:
        category = _CATEGORIES[0]
    year = query.get("year", [_YEARS[0]])[0]
    if year not in _YEARS:
        year = _YEARS[0]
    items = _items_for(category, year)
    rows = ""
    for doc_id, title, cat, yr in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="category">{cat}</span>'
            f'<span class="year">{yr}</span></div>\n'
        )
    cat_opts = _options(_CATEGORIES, category)
    yr_opts = _options(_YEARS, year)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Multi-Filter Archive</title></head><body>"
        "<div class='container'><h1>Multi-Filter Document List</h1>"
        f"<select id='filter-category'>{cat_opts}</select>"
        f"<select id='filter-year'>{yr_opts}</select>"
        f"<div class='item-list'>{rows}</div></div>"
        "<script>"
        "function navigate() {"
        "  var c = document.getElementById('filter-category').value;"
        "  var y = document.getElementById('filter-year').value;"
        "  window.location.href = '?scenario=" + _SCENARIO + "&category=' + c + '&year=' + y;"
        "}"
        "document.getElementById('filter-category').addEventListener('change', navigate);"
        "document.getElementById('filter-year').addEventListener('change', navigate);"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
