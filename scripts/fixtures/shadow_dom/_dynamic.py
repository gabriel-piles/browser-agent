"""Dynamic fixture renderer for shadow_dom scenario.

10 items rendered inside a custom element with Shadow DOM. The
items are attached via attachShadow({mode: 'open'}) and are not
accessible via standard CSS selectors — only via shadowRoot.
"""

from __future__ import annotations

_TOTAL_ITEMS = 10
_SCENARIO = "shadow_dom"


def _shadow_items_html() -> str:
    """Render 10 item rows HTML for injection into the shadow root."""
    rows = ""
    for i in range(1, _TOTAL_ITEMS + 1):
        title = f"Shadow Document #{i}"
        date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={_SCENARIO}">{title}</a></h3>'
            f'<span class="date">{date}</span></div>'
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
    """Handle /doc/N detail pages."""
    if path.startswith("/doc/"):
        try:
            doc_id = int(path.split("/")[-1])
        except ValueError:
            return None
        return _doc_detail(doc_id), "text/html; charset=utf-8", 200
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with a shadow host and JS to populate it."""
    items_html = _shadow_items_html()
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Shadow DOM Archive</title></head><body>"
        "<div class='container'><h1>Shadow DOM Archive</h1>"
        '<div id="shadow-host"></div>'
        "</div>"
        "<script>"
        "var host = document.getElementById('shadow-host');"
        "var shadow = host.attachShadow({mode: 'open'});"
        'shadow.innerHTML = \'<div class="item-list">' + items_html + "</div>';"
        "</script></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
