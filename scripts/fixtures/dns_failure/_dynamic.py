"""Dynamic fixture renderer for dns_failure scenario.

Serves 10 items on one page. The page references broken
sub-resources on a non-existent domain (img + iframe) that will
fail to load, but the item content remains extractable. Tests
that the agent handles broken sub-resources without losing data.
"""

from __future__ import annotations

_TOTAL_ITEMS = 10


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routing needed for this scenario."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 10 items and broken sub-resource references."""
    scenario = query.get("scenario", ["dns_failure"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span>'
            '<img src="http://nonexistent.invalid/broken.png" alt="broken image">'
            "</div>\n"
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>DNS Failure Archive</title></head><body>"
        "<div class='container'><h1>DNS Failure Archive</h1>"
        '<iframe src="http://nonexistent.invalid/frame.html"></iframe>'
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
