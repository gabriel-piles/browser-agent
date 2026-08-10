"""Dynamic fixture renderer for slow_responses scenario.

Simulates a site where pages sometimes take longer to respond.
The first 2 requests respond normally, subsequent requests add
a 3-second delay. Tests that the agent's wait_for_page_ready and
navigation timeouts handle slow responses without crashing.
"""

from __future__ import annotations

import time

_SLOW_DELAY_S = 3.0
_NORMAL_REQUESTS = 2
_request_count: dict[str, int] = {}


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Add artificial delay to responses after the first few requests."""
    scenario = query.get("scenario", ["slow_responses"])[0]
    count = _request_count.get(scenario, 0)
    _request_count[scenario] = count + 1
    if count >= _NORMAL_REQUESTS:
        time.sleep(_SLOW_DELAY_S)
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render a page with 10 items that link to slow detail pages."""
    scenario = query.get("scenario", ["slow_responses"])[0]
    rows = ""
    for i in range(1, 11):
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">Slow Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Slow Archive</title></head><body>"
        "<div class='container'><h1>Slow Document Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
