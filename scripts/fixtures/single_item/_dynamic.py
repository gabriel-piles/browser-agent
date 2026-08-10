"""Dynamic fixture renderer for single_item scenario.

Serves exactly one item on a single page with no pagination.
"""

from __future__ import annotations

_SCENARIO = "single_item"


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes for this scenario."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render a page with a single document and no pagination."""
    rows = (
        f'<div class="item"><h3>'
        f'<a href="/doc/1?scenario={_SCENARIO}">Only Document</a>'
        f'</h3><span class="date">2024-01-01</span></div>\n'
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Single Item Archive</title></head><body>"
        "<div class='container'><h1>Single Item Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
