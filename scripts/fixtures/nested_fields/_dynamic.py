"""Dynamic fixture renderer for nested_fields scenario.

Serves 10 items, each with a title plus nested metadata fields:
author, date, and tags (two tags per item). Agents must extract
every nested field and store it in save_record's data JSON.
"""

from __future__ import annotations

_TOTAL = 10


def _item(i: int, scenario: str) -> str:
    """Return one item row with nested author/date/tags metadata."""
    title = f"Nested Report {i}"
    date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
    author = f"Author {i}"
    tags = f"tag{i}a, tag{i}b"
    return (
        f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">{title}</a></h3>'
        f'<span class="date">{date}</span>'
        f'<span class="author">{author}</span>'
        f'<span class="tags">{tags}</span></div>\n'
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
            f"<title>Nested Report {doc_id}</title></head>"
            f"<body><h1>Nested Report {doc_id}</h1></body></html>",
            "text/html; charset=utf-8",
            200,
        )
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render 10 items with nested metadata fields."""
    scenario = query.get("scenario", ["nested_fields"])[0]
    rows = "".join(_item(i, scenario) for i in range(1, _TOTAL + 1))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Nested Fields Archive</title></head><body>"
        "<div class='container'><h1>Nested Fields Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
