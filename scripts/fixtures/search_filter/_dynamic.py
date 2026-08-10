"""Dynamic fixture renderer for search_filter scenario.

A search form filters items by keyword. Searching "report" returns 8
items, "memo" returns 5, and an empty search returns all 10. Tests that
the agent can drive a search input and submit to filter a listing.
"""

from __future__ import annotations

_ALL_ITEMS = [
    (1, "Quarterly Report Q1", "2024-01-15"),
    (2, "Annual Report 2023", "2024-01-30"),
    (3, "Financial Report Summary", "2024-02-20"),
    (4, "Compliance Report", "2024-03-10"),
    (5, "Budget Report Draft", "2024-03-15"),
    (6, "Report Memo January", "2024-01-05"),
    (7, "Report Memo February", "2024-02-05"),
    (8, "Quarterly Memo Report", "2024-04-15"),
    (9, "Internal Memo March", "2024-03-01"),
    (10, "Project Memo Review", "2024-04-01"),
]

_KEYWORDS = {"report": 8, "memo": 5}


def _matches(title: str, q: str) -> bool:
    """True when the title contains the query term (case-insensitive)."""
    return q.lower() in title.lower()


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes; all serving goes through the index renderer."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the search form plus filtered item list based on ?q=."""
    scenario = query.get("scenario", ["search_filter"])[0]
    q = query.get("q", [""])[0]
    items = [it for it in _ALL_ITEMS if not q or _matches(it[1], q)]
    rows = ""
    for doc_id, title, date in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={scenario}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Search Filter Archive</title></head><body>"
        "<div class='container'><h1>Document Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
