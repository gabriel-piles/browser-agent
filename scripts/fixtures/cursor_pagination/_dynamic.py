"""Dynamic fixture renderer for cursor_pagination scenario."""

from __future__ import annotations

_ITEMS_PER_PAGE = 10
_TOTAL_ITEMS = 30


def _items_after(after: int) -> list[tuple[int, str, str]]:
    """Return up to _ITEMS_PER_PAGE items with id strictly greater than after."""
    start = after + 1
    end = min(start + _ITEMS_PER_PAGE, _TOTAL_ITEMS + 1)
    items: list[tuple[int, str, str]] = []
    for i in range(start, end):
        items.append((i, f"Document Report #{i}", f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"))
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


def index(query: dict[str, list[str]]) -> str:
    """Render the page for the cursor; includes next-cursor link if more items."""
    scenario = query.get("scenario", ["cursor_pagination"])[0]
    after = int(query.get("after", ["0"])[0])
    after = max(0, min(after, _TOTAL_ITEMS))
    items = _items_after(after)
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={scenario}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    next_cursor = ""
    if items:
        last_id = items[-1][0]
        if last_id < _TOTAL_ITEMS:
            next_cursor = f'<a class="next-cursor" href="?scenario={scenario}&after={last_id}">Next</a>'
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Cursor Pagination Archive</title></head><body>"
        "<div class='container'><h1>Archive</h1>"
        f"<div class='item-list'>{rows}</div>"
        f"<div class='pagination'>{next_cursor}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
