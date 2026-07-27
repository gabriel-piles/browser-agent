"""One navigation path the prompt requires the scraper to cover.

Declared up front via the ``declare_paths`` tool so coverage becomes an
auditable checklist with a denominator rather than a silent judgement
call. The verifier tracks which declared paths have been visited and
echoes the remaining ones so the agent cannot silently truncate its
mental model of the prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExpectedPath(BaseModel):
    """A prompt-described navigation path/filter/page that yields PDFs."""

    path: str = Field(description="Human-readable path/filter/page from the prompt.")
    expected_count_hint: str = Field(
        default="",
        description="What the prompt or site advertises for this path (e.g. 'all years').",
    )
    visited: bool = Field(default=False, description="True once the agent has checked this path.")
