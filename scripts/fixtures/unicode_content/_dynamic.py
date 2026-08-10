# -*- coding: utf-8 -*-
"""Dynamic fixture renderer for unicode_content scenario.

Serves 10 items whose titles contain accented Latin characters,
CJK characters, and emoji. The file is UTF-8 encoded and the page
declares charset=utf-8 so agents must handle Unicode correctly.
"""

from __future__ import annotations

_TITLES = [
    "Café Report 1",
    "Naïve Analysis 2",
    "日本語 Report 3",
    "📄 Document 4",
    "Sørensen Study 5",
    "Résumé Summary 6",
    "Überblick 7",
    "中文标题 Report 8",
    "📝 Notes 9",
    "Zürich File 10",
]


def _item(i: int, scenario: str) -> str:
    """Return one item row with a Unicode title."""
    date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
    title = _TITLES[i - 1]
    return (
        f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">{title}</a></h3>'
        f'<span class="date">{date}</span></div>\n'
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages linked from the index."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        title = _TITLES[doc_id - 1]
        return (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>"
            f"</head><body><h1>{title}</h1></body></html>",
            "text/html; charset=utf-8",
            200,
        )
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render 10 items with Unicode titles."""
    scenario = query.get("scenario", ["unicode_content"])[0]
    rows = "".join(_item(i, scenario) for i in range(1, len(_TITLES) + 1))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Unicode Content Archive</title></head><body>"
        "<div class='container'><h1>Unicode Content Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
