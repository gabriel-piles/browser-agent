"""Dynamic fixture renderer for empty_page scenario.

Serves a valid page with a .item-list container but zero .item
elements, exercising the crawler's empty-result path.
"""

from __future__ import annotations


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes for this scenario."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render a valid page with an empty item-list container."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Empty Archive</title></head><body>"
        "<div class='container'><h1>Empty Archive</h1>"
        "<div class='item-list'></div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
