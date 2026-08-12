"""Dynamic fixture renderer for discovery_undercollect scenario.

4 filter values, 5 items each (20 total). The 4th filter value
("decisions") is hidden behind a "More filters" expand button that
adds the option to the <select> via JS. The discovery self-check
must detect UNDER-COLLECTED and trigger a repair to find all 20.
"""

from __future__ import annotations

_CATEGORIES = ["reports", "resolutions", "measures"]
_HIDDEN_CATEGORY = "decisions"
_ITEMS_PER_CATEGORY = 5
_SCENARIO = "discovery_undercollect"


def _items_for(category: str) -> list[tuple[int, str, str]]:
    """Return (id, title, category) tuples for the given category."""
    all_cats = _CATEGORIES + [_HIDDEN_CATEGORY]
    if category not in all_cats:
        category = _CATEGORIES[0]
    base = all_cats.index(category) * _ITEMS_PER_CATEGORY
    items: list[tuple[int, str, str]] = []
    for i in range(_ITEMS_PER_CATEGORY):
        doc_id = base + i + 1
        title = f"{category.title()} Item #{doc_id}"
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


def _options_html(category: str, show_hidden: bool = False) -> str:
    """Render the select options — includes the hidden category when visible."""
    all_cats = _CATEGORIES + [_HIDDEN_CATEGORY]
    options = ""
    for cat in all_cats:
        if cat == _HIDDEN_CATEGORY and not show_hidden:
            continue
        selected = " selected" if cat == category else ""
        options += f"<option value='{cat}'{selected}>{cat.title()}</option>"
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
    all_cats = _CATEGORIES + [_HIDDEN_CATEGORY]
    category = query.get("category", [_CATEGORIES[0]])[0]
    if category not in all_cats:
        category = _CATEGORIES[0]
    more_clicked = query.get("more", ["0"])[0] == "1"
    show_hidden = more_clicked or category == _HIDDEN_CATEGORY
    items = _items_for(category)
    options = _options_html(category, show_hidden)
    rows = _rows_html(items)
    more_filters_display = "none" if show_hidden else "inline-block"
    more_param = "&more=1" if more_clicked else ""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Under-Collect Archive</title></head><body>"
        "<div class='container'><h1>Filtered Document List</h1>"
        f"<select id='filter-category' onchange=\"window.location.href='?scenario={_SCENARIO}&category='+this.value+'{more_param}'\">{options}</select>"
        f"<button id='more-filters' style='display:{more_filters_display}' onclick=\""
        "var sel=document.getElementById('filter-category');"
        "var opt=document.createElement('option');"
        "opt.value='decisions';opt.textContent='Decisions';"
        "sel.appendChild(opt);this.style.display='none';"
        f"window.location.href='?scenario={_SCENARIO}&category='+sel.value+'&more=1';"
        f'">More filters</button>'
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
