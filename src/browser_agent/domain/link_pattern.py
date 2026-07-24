"""One URL-pattern group discovered during ``analyze``.

Groups links by their href path directory or file extension and
provides a ready-to-use CSS attribute selector (``a[href*="..."]`` or
``a[href$="..."]``) so the agent can extract the right links without
blind-probing selectors that return zero results.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LinkPattern(BaseModel):
    """A group of links sharing a common href path or extension.

    ``selector`` is a CSS attribute selector the agent can pass directly
    to ``explore_page(action='extract', selector=...)`` — it is
    guaranteed to match at least 2 links on the page (groups with fewer
    are not emitted).
    """

    selector: str = Field(
        description="CSS attribute selector, e.g. a[href*='/reports/pdfs/'] or a[href$='.pdf'].",
    )
    count: int = Field(description="Number of links matching this pattern.")
    sample_hrefs: list[str] = Field(
        default_factory=list,
        description="Up to 3 sample hrefs from this group.",
    )
