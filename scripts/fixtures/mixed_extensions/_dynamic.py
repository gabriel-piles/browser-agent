"""Dynamic fixture renderer for mixed_extensions scenario.

Serves a page with 14 items, each linking to a document with a
different extension: .pdf (3), .PDF (3), .doc (3), .DOC (3), and
2 HTML links. Tests that the agent handles case-insensitive file
extensions and classifies document types correctly.
"""

from __future__ import annotations

_FILES = [
    ("doc1.pdf", "pdf", "lower"),
    ("doc2.pdf", "pdf", "lower"),
    ("doc3.pdf", "pdf", "lower"),
    ("doc4.PDF", "pdf", "upper"),
    ("doc5.PDF", "pdf", "upper"),
    ("doc6.PDF", "pdf", "upper"),
    ("doc7.doc", "doc", "lower"),
    ("doc8.doc", "doc", "lower"),
    ("doc9.doc", "doc", "lower"),
    ("doc10.DOC", "doc", "upper"),
    ("doc11.DOC", "doc", "upper"),
    ("doc12.DOC", "doc", "upper"),
    ("doc13.html", "html", "lower"),
    ("doc14.html", "html", "lower"),
]


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Handle /file/ routes for document downloads."""
    if path.startswith("/file/"):
        return None  # Static file serving handles these
    return None


def index(query: dict[str, list[str]]) -> str:
    """Render the page with mixed-extension document links."""
    scenario = query.get("scenario", ["mixed_extensions"])[0]
    rows = ""
    for fname, doc_type, case in _FILES:
        title = f"Document {fname}"
        rows += (
            f'<div class="item"><h3><a href="/file/{fname}?scenario={scenario}">{title}</a></h3>'
            f'<span class="doc-type">{doc_type}</span>'
            f'<span class="case">{case}</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Mixed Extensions Archive</title></head><body>"
        "<div class='container'><h1>Mixed Document Extensions</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
