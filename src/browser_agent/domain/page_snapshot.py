"""The structured result of an ``explore_page`` tool call.

After the browser session performs the requested action it returns
a :class:`PageSnapshot` so the agent can reason about the current
state of the page: what URL it's on, what the cleaned HTML looks like,
any extracted elements (links, counts, text), whether the URL changed
after an action, the current scroll height, and any error that
occurred.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedElement(BaseModel):
    """One element returned by an ``extract`` action."""

    tag: str = Field(description="HTML tag name (e.g. 'a', 'button', 'div').")
    text: str = Field(default="", description="Visible text content, truncated.")
    href: str = Field(default="", description="href attribute if present, else empty.")


class PageSnapshot(BaseModel):
    """The browser's view of the page after performing an action."""

    url: str = Field(description="Current page URL after the action.")
    title: str = Field(default="", description="Page <title>.")
    summary: str = Field(
        default="",
        description="One-line summary: char counts, truncation status.",
    )
    cleaned_html: str = Field(
        default="",
        description=(
            "Token-optimised HTML snapshot (scripts, styles, svg stripped). "
            "Present for navigate/scroll/click/wait actions so the agent can "
            "read the DOM. May be empty for extract actions."
        ),
    )
    extracted: list[ExtractedElement] = Field(
        default_factory=list,
        description="Elements matching the extract selector (text + href).",
    )
    extracted_count: int = Field(
        default=0,
        description="Total number of elements matched by the extract selector.",
    )
    scroll_height: int = Field(
        default=0,
        description=(
            "Current document.body.scrollHeight in pixels. Compare before/after scroll to detect lazy-loaded content."
        ),
    )
    previous_url: str = Field(
        default="",
        description="Page URL before the action. Compare with url to detect navigation.",
    )
    url_changed: bool = Field(
        default=False,
        description="True when the URL changed as a result of the action (e.g. filter click).",
    )
    error: str = Field(
        default="",
        description=(
            "Empty on success. On failure, a human-readable error message "
            "(e.g. 'click: no element matches selector'). "
            "When this is set, cleaned_html and extracted may be empty."
        ),
    )
    action_performed: str = Field(
        description="Human-readable description of the action that was executed.",
    )

    def is_empty(self) -> bool:
        """True when the snapshot has no HTML and no extracted elements."""
        return not self.cleaned_html and not self.extracted
