"""A field the LLM chose to skip when drafting the mapping.

The :class:`ProposeMappingUseCase` records a :class:`SkippedField`
for every catalog field it could not place on a Uwazi property. The
mapping file persists these so a reviewer can re-include them after
manually extending the Uwazi template.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkippedField(BaseModel):
    """A catalog field the LLM decided to drop."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="Catalog field name.")
    reason: str = Field(description="One of 'no_match', 'duplicate', 'incompatible_type'.")
    notes: str | None = Field(default=None, description="Human-readable explanation.")
