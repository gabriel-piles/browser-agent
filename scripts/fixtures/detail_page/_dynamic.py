"""Dynamic fixture renderer for detail_page scenario."""

from __future__ import annotations

_ITEMS_PER_PAGE = 10


def _page_items() -> list[tuple[int, str, str, str, str]]:
    """Return (id, title, date, author, description) tuples."""
    authors = ["Office of the Commissioner", "Secretariat", "Rapporteurship", "IACHR", "Commission"]
    items: list[tuple[int, str, str, str, str]] = []
    for i in range(1, _ITEMS_PER_PAGE + 1):
        title = f"Document Report #{i}"
        date = f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        author = authors[(i - 1) % len(authors)]
        desc = f"Full text of document {i} for the archive."
        items.append((i, title, date, author, desc))
    return items


def _doc_detail(doc_id: int) -> str:
    """Render a detail page with title, date, author, description, PDF link."""
    authors = ["Office of the Commissioner", "Secretariat", "Rapporteurship", "IACHR", "Commission"]
    title = f"Document Report #{doc_id}"
    date = f"2024-{(doc_id % 12) + 1:02d}-{(doc_id % 28) + 1:02d}"
    author = authors[(doc_id - 1) % len(authors)]
    desc = f"Full text of document {doc_id} for the archive."
    pdf = f"/pdf/doc{doc_id}.pdf?scenario=detail_page"
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<title>{title}</title></head><body>"
        f'<div class="container">'
        f'<h1 class="title">{title}</h1>'
        f'<p class="date">{date}</p>'
        f'<p class="author">{author}</p>'
        f'<p class="description">{desc}</p>'
        f'<a class="pdf-link" href="{pdf}">Download PDF</a>'
        "</div></body></html>"
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
    """Render the listing page linking to detail pages."""
    scenario = query.get("scenario", ["detail_page"])[0]
    items = _page_items()
    rows = ""
    for doc_id, title, date, author, _desc in items:
        rows += (
            f'<div class="item"><h3><a href="/doc/{doc_id}?scenario={scenario}">{title}</a></h3>'
            f'<span class="date">{date}</span>'
            f'<span class="author">{author}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Detail Page Archive</title></head><body>"
        "<div class='container'><h1>Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
