"""Dynamic fixture renderer for rate_limit_429 scenario.

Serves 3 pages of 10 items each (30 total). After every 2
requests the server returns 429 with a Retry-After: 1 header.
Tests that the agent respects rate-limiting and backs off.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 3
_request_count: int = 0


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int, dict[str, str]] | None:
    """Return 429 on every 3rd request (after every 2 requests)."""
    global _request_count
    _request_count += 1
    if _request_count % 3 == 0:
        return ("Rate limited", "text/plain", 429, {"Retry-After": "1"})
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number."""
    scenario = query.get("scenario", ["rate_limit_429"])[0]
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    rows = _page_rows(page, scenario)
    nav = ""
    if page < _TOTAL_PAGES:
        nav = f'<a class="next" href="/?scenario={scenario}&page={page + 1}">Next</a>'
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="/?scenario={scenario}&page={page - 1}">Previous</a> '
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Rate Limit 429 Archive</title></head><body>"
        f"<div class='container'><h1>Archive — Page {page}</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{prev} {nav}</div></div></body></html>"
    )


def _page_rows(page: int, scenario: str) -> str:
    """Return the HTML rows for the given page."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    rows = ""
    for i in range(start, start + _ITEMS_PER_PAGE):
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return rows


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
