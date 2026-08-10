"""Dynamic fixture renderer for iframe_content scenario.

The listing page embeds an <iframe src="/inner.html?scenario=iframe_content">
that renders 10 items inside <div class="item-list">. The iframe content
must be accessed via frame.contentDocument or zendriver's iframe API.
"""

from __future__ import annotations

_TOTAL_ITEMS = 10
_SCENARIO = "iframe_content"


def _rows_html() -> str:
    """Render 10 item rows HTML inside the iframe."""
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        title = f"Iframe Document #{i}"
        date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>\n'
        )
    return rows


def _doc_detail(doc_id: int) -> str:
    """Render a detail page for a single document."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Document #{doc_id}</title></head><body>"
        f"<div class='container'><h1>Document #{doc_id}</h1>"
        f"<p class='date'>2024-01-{doc_id:02d}</p></div></body></html>"
    )


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /inner.html and /doc/N detail pages."""
    if path == "/inner.html":
        return _inner_page(), "text/html; charset=utf-8", 200
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return _doc_detail(doc_id), "text/html; charset=utf-8", 200
    return None


def _inner_page() -> str:
    """Render the iframe inner HTML page with the item list."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Inner Iframe Content</title></head><body>"
        f"<div class='item-list'>{_rows_html()}</div>"
        "</body></html>"
    )


def index(query: dict[str, list[str]]) -> str:
    """Render the outer page with an iframe pointing to /inner.html."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Iframe Content Archive</title></head><body>"
        "<div class='container'><h1>Iframe Content Archive</h1>"
        f'<iframe id="content-frame" src="/inner.html?scenario={_SCENARIO}" '
        'width="100%" height="600"></iframe>'
        "</div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
