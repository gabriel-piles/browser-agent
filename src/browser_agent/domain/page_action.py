"""A single action the agent asks the browser session to perform.

The agent describes *what* it wants (navigate, click, scroll, fill,
extract) and the session adapter translates that into zendriver calls.
Keeping this as a pydantic model gives pydantic-ai a clean JSON schema
for the tool parameter, so the LLM's tool call is structured rather
than free-form text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActionType = Literal["navigate", "click", "scroll", "fill", "select", "extract", "wait"]


class PageAction(BaseModel):
    """One atomic action for the persistent browser session.

    - ``navigate``  — open ``url`` in the current tab (first action
      of any exploration; subsequent calls reuse the same tab).
    - ``click``     — click the element matching ``selector``.
    - ``scroll``    — scroll to the bottom of the page (or by
      ``scroll_pixels`` if given). Use to trigger lazy-loaded content.
    - ``fill``      — type ``value`` into the input matching
      ``selector``.
    - ``select``     — pick ``value`` in the ``<select>`` matching
      ``selector``.
    - ``extract``   — run ``selector`` (CSS) against the page and
      return matching elements' text + href. Use ``selector`` to
      extract links, counts, or verify filters reacted.
    - ``wait``      — sleep ``wait_seconds`` for AJAX to settle.
    """

    action: ActionType = Field(
        description="What to do: navigate, click, scroll, fill, select, extract, or wait.",
    )
    url: str | None = Field(
        default=None,
        description="URL to navigate to. Required for 'navigate'; ignored otherwise.",
    )
    selector: str | None = Field(
        default=None,
        description=(
            "Standard CSS selector. Required for click/fill/select/extract; "
            "ignored otherwise. Playwright-only pseudo-classes are NOT supported."
        ),
    )
    value: str | None = Field(
        default=None,
        description=("Text to type (fill), option value to select (select), or ignored for other actions."),
    )
    scroll_pixels: int | None = Field(
        default=None,
        description=("Pixels to scroll down. If omitted, scrolls to the bottom of the page (document.body.scrollHeight)."),
    )
    wait_seconds: float | None = Field(
        default=None,
        description="Seconds to sleep. Defaults to 1.0 for 'wait' action.",
    )
