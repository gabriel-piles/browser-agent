"""Dynamic fixture renderer for long_text scenario.

Serves 5 items. Each title is a 500-character string and each
description is a 2000-character string, exercising how the agent
handles very long text fields without truncation or corruption.
"""

from __future__ import annotations

_TOTAL = 5
_TITLE_LEN = 500
_DESC_LEN = 2000


def _title(i: int) -> str:
    """Return a 500-character title for item i."""
    base = f"Report {i} Title "
    return (base * (_TITLE_LEN // len(base) + 1))[:_TITLE_LEN]


def _desc(i: int) -> str:
    """Return a 2000-character description for item i."""
    base = f"Body text for report {i}. "
    return (base * (_DESC_LEN // len(base) + 1))[:_DESC_LEN]


def _item(i: int, scenario: str) -> str:
    """Return one item row with a long title and long description."""
    title = _title(i)
    desc = _desc(i)
    return (
        f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">{title}</a></h3>'
        f'<p class="description">{desc}</p></div>\n'
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
            f"<title>{_title(doc_id)}</title></head>"
            f"<body><h1>{_title(doc_id)}</h1>"
            f"<p class='description'>{_desc(doc_id)}</p></body></html>",
            "text/html; charset=utf-8",
            200,
        )
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render 5 items with long titles and descriptions."""
    scenario = query.get("scenario", ["long_text"])[0]
    rows = "".join(_item(i, scenario) for i in range(1, _TOTAL + 1))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Long Text Archive</title></head><body>"
        "<div class='container'><h1>Long Text Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
