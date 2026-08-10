"""Dynamic fixture renderer for numbered_pagination scenario."""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_PAGES = 5


def _page_items(page: int) -> list[tuple[int, str, str]]:
    """Return (id, title, date) tuples for the given page number."""
    start = (page - 1) * _ITEMS_PER_PAGE + 1
    items: list[tuple[int, str, str]] = []
    for i in range(start, start + _ITEMS_PER_PAGE):
        items.append((i, f"Document Report #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
    return items


def _numbered_nav(scenario: str, page: int) -> str:
    """Build numbered page links plus prev/next."""
    links = ""
    for n in range(1, _TOTAL_PAGES + 1):
        links += f'<a class="page-link" href="?scenario={scenario}&page={n}">{n}</a> '
    prev = ""
    if page > 1:
        prev = f'<a class="prev" href="?scenario={scenario}&page={page - 1}">Previous</a> '
    nxt = ""
    if page < _TOTAL_PAGES:
        nxt = f'<a class="next" href="?scenario={scenario}&page={page + 1}">Next</a>'
    return f"{prev}{links}{nxt}"


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the requested page number with numbered nav."""
    scenario = query.get("scenario", ["numbered_pagination"])[0]
    page = int(query.get("page", ["1"])[0])
    page = max(1, min(page, _TOTAL_PAGES))
    items = _page_items(page)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={scenario}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Numbered Pagination Archive</title></head><body>"
        f"<div class='container'><h1>Archive — Page {page}</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{_numbered_nav(scenario, page)}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
