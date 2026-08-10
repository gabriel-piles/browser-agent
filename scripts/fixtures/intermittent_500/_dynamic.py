"""Dynamic fixture renderer for intermittent_500 scenario.

Serves 5 pages of 10 items each (50 total). Pages 2 and 4 return
HTTP 500 on the FIRST request, then succeed on retry. Tests that
the agent retries failed page loads instead of giving up.
"""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 5
_FAIL_PAGES = (2, 4)
_request_counts: dict[str, int] = {}


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Return 500 on first request for pages 2 and 4; fall through otherwise."""
    if path not in ("/", "/index.html"):
        return None
    page = int(query.get("page", ["1"])[0])
    if page not in _FAIL_PAGES:
        return None
    key = f"page_{page}"
    count = _request_counts.get(key, 0)
    _request_counts[key] = count + 1
    if count == 0:
        return "Internal Server Error", "text/plain; charset=utf-8", 500
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number."""
    scenario = query.get("scenario", ["intermittent_500"])[0]
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
        "<title>Intermittent 500 Archive</title></head><body>"
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
