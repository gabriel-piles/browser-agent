"""One crawl-value -> Uwazi-value mapping for a single thesaurus.

Written by ``step_2_uwazi_match.py``: each entry maps a value scraped from
``metadata.db`` to its canonical form on the Uwazi thesaurus, with
a ``needs_review`` flag for the human reviewer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ThesaurusMappingEntry(BaseModel):
    """One crawl value's mapping to a thesaurus value."""

    model_config = ConfigDict(extra="forbid")

    crawl_value: str = Field(description="The value as it appears in metadata.db.")
    uwazi_value: str | None = Field(
        default=None,
        description="The canonical thesaurus value to substitute; None when the LLM could not place it.",
    )
    needs_review: bool = Field(
        default=True,
        description="True when the LLM (or the human reviewer) flagged this entry as low confidence.",
    )
    occurrences: int = Field(default=0, description="How many rows in metadata.db hold this crawl value.")
    note: str | None = Field(default=None, description="Optional human note.")
