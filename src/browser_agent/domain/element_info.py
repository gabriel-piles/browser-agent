"""One extracted element in a structured page analysis.

Carries the tag, visible text, href (for links), a suggested CSS selector,
and an ``extra`` dict for type/name/role attributes that help the agent
identify the element.  Simple leaf data — no methods, no children.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ElementInfo(BaseModel):
    """One element discovered by a structured ``analyze`` action.

    ``selector`` is a best-effort CSS selector the agent can use with
    click/fill/select/extract — may match multiple elements when
    the element has no id and shares tag+class with others.
    """

    tag: str = Field(description="HTML tag name (e.g. 'a', 'button', 'div').")
    text: str = Field(default="", description="Visible text content, truncated.")
    href: str = Field(default="", description="href attribute if present, else empty.")
    selector: str = Field(
        default="",
        description=("Suggested CSS selector (by id, class, or tag). May match multiple elements — refine if needed."),
    )
    extra: dict[str, str] = Field(
        default_factory=dict,
        description="Additional attributes: type, name, role, aria-label, etc.",
    )
