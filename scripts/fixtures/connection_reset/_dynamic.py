"""Dynamic fixture renderer for connection_reset scenario.

Serves 10 items on one page. Every 3rd request triggers a
connection reset (status 999 sentinel) — the fixture server
closes the socket mid-response. Tests that the agent recovers
from dropped connections.
"""

from __future__ import annotations

_TOTAL_ITEMS = 10
_request_count: int = 0


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Reset the connection on every 3rd request."""
    global _request_count
    _request_count += 1
    if _request_count % 3 == 0:
        return "", "text/html; charset=utf-8", 999
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 10 items."""
    scenario = query.get("scenario", ["connection_reset"])[0]
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Connection Reset Archive</title></head><body>"
        "<div class='container'><h1>Connection Reset Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
