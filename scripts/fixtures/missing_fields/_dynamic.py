"""Dynamic fixture renderer for missing_fields scenario.

Serves 10 items. Items 1-5 carry title, date, and author spans.
Items 6-10 carry only title and date (the author span is omitted)
so the agent must tolerate missing fields rather than failing.
"""

from __future__ import annotations

_TOTAL = 10
_WITH_AUTHOR = 5


def _item(i: int, scenario: str) -> str:
    """Return one item row; author span present only for items 1-5."""
    title = f"Report {i}"
    date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
    author = f'<span class="author">Author {i}</span>' if i <= _WITH_AUTHOR else ""
    return (
        f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">{title}</a></h3>'
        f'<span class="date">{date}</span>{author}</div>\n'
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages linked from the index."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>Report {doc_id}</title></head>"
            f"<body><h1>Report {doc_id}</h1></body></html>",
            "text/html; charset=utf-8",
            200,
        )
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render 10 items with author span present only on items 1-5."""
    scenario = query.get("scenario", ["missing_fields"])[0]
    rows = "".join(_item(i, scenario) for i in range(1, _TOTAL + 1))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Missing Fields Archive</title></head><body>"
        "<div class='container'><h1>Missing Fields Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
