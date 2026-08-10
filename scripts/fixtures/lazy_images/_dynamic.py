"""Dynamic fixture renderer for lazy_images scenario.

Serves 10 items whose images use loading="lazy" and data-src. The
img src is empty until the browser scrolls the image into view.
"""

from __future__ import annotations

_SCENARIO = "lazy_images"
_TOTAL_ITEMS = 10


def _item(doc_id: int) -> str:
    """Render a single item row with a lazy image."""
    title = f"Title {doc_id}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    return (
        f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={_SCENARIO}">{title}</a></h3>'
        f'<span class="date">{date}</span>'
        f'<img loading="lazy" data-src="/img/{doc_id}.png" src=""></div>\n'
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """No custom routes for this scenario."""
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with 10 items holding lazy-loaded images."""
    rows = ""
    for doc_id in range(1, _TOTAL_ITEMS + 1):
        rows += _item(doc_id)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Lazy Images Archive</title></head><body>"
        "<div class='container'><h1>Lazy Images Archive</h1>"
        f"<div class='item-list'>{rows}</div></div>"
        "<script>"
        "var imgs = document.querySelectorAll('img[loading=lazy]');"
        "var io = new IntersectionObserver(function(entries) {"
        "  entries.forEach(function(e) {"
        "    if (e.isIntersecting) {"
        "      e.target.src = e.target.dataset.src;"
        "      io.unobserve(e.target);"
        "    }"
        "  });"
        "});"
        "imgs.forEach(function(img) { io.observe(img); });"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
