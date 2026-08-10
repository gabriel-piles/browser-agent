"""Dynamic fixture renderer for new_tab scenario.

Serves 10 items whose links open in a new tab via target="_blank".
"""

from __future__ import annotations

_SCENARIO = "new_tab"
_TOTAL_ITEMS = 10


def _item(doc_id: int) -> str:
    """Render a single item row with a target=_blank link."""
    title = f"Title {doc_id}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    return (
        f'<div class="item"><h3>'
        f'<a href="/doc/{doc_id}?scenario={_SCENARIO}" target="_blank">{title}</a>'
        f'</h3><span class="date">{date}</span></div>\n'
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes for this scenario."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 10 new-tab links."""
    rows = ""
    for doc_id in range(1, _TOTAL_ITEMS + 1):
        rows += _item(doc_id)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>New Tab Archive</title></head><body>"
        "<div class='container'><h1>New Tab Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
