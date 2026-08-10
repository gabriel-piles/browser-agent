"""Dynamic fixture renderer for stale_element scenario.

Serves 10 items inside a list container. Two seconds after load,
client-side JS removes and re-adds every item element, so agents
that captured element references before the re-render go stale.
"""

from __future__ import annotations

_TOTAL = 10


def _item(i: int, scenario: str) -> str:
    """Return one item row for index i."""
    date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
    return (
        f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">'
        f"Report {i}</a></h3>"
        f'<span class="date">{date}</span></div>\n'
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /doc/N detail pages linked from the index."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        title = f"Report {doc_id}"
        return (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title>"
            f"</head><body><h1>{title}</h1></body></html>",
            "text/html; charset=utf-8",
            200,
        )
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render 10 items plus the re-render-after-2s script."""
    scenario = query.get("scenario", ["stale_element"])[0]
    rows = "".join(_item(i, scenario) for i in range(1, _TOTAL + 1))
    script = (
        "setTimeout(function() { var list = document.getElementById('item-list');"
        " var html = list.innerHTML; list.innerHTML = ''; list.innerHTML = html; }, 2000);"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Stale Element Archive</title></head><body>"
        "<div class='container'><h1>Stale Element Archive</h1>"
        f'<div class="item-list" id="item-list">{rows}</div>'
        f"<script>{script}</script></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
