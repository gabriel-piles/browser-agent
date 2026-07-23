"""Structured analysis of a web page's interactive surface.

Returned by the ``explore_page(action='analyze')`` call.  Instead of
dumping raw HTML, ``PageStructure`` gives the agent a compact,
selector-rich view of the page: every link, button, form input,
heading, table, pagination element, and filter control — each with
a suggested CSS selector so the agent can immediately use it with
click/fill/select/extract.

The analysis is intentionally flat (no DOM tree) — the agent does
not need the nesting; it needs selectors and counts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.element_info import ElementInfo


class PageStructure(BaseModel):
    """Selector-oriented page analysis: links, buttons, forms, tables.

    Every element carries a ``selector`` field the agent can pass
    directly to click/fill/select/extract.  The analysis is built
    from the current DOM state (after any pending mutations),
    so scrolling or clicking before a second ``analyze`` will
    reflect the new state.
    """

    url: str = Field(description="Page URL at the time of analysis.")
    title: str = Field(default="", description="Page <title>.")
    links: list[ElementInfo] = Field(
        default_factory=list,
        description="All <a> elements with an href attribute.",
    )
    buttons: list[ElementInfo] = Field(
        default_factory=list,
        description="Buttons: <button>, input[type=submit], input[type=button].",
    )
    inputs: list[ElementInfo] = Field(
        default_factory=list,
        description="Form inputs: <input>, <select>, <textarea>.",
    )
    headings: list[ElementInfo] = Field(
        default_factory=list,
        description="Heading elements (<h1> through <h6>).",
    )
    tables: list[ElementInfo] = Field(
        default_factory=list,
        description="<table> elements with row count and column headers in extra.",
    )
    pagination: list[ElementInfo] = Field(
        default_factory=list,
        description="Likely pagination links / buttons (by text heuristics).",
    )
    filters: list[ElementInfo] = Field(
        default_factory=list,
        description="Likely filter controls (selects, checkboxes, filter buttons).",
    )
