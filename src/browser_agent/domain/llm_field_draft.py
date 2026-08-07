"""One field in the LLM's mapping draft."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LlmFieldDraft(BaseModel):
    """One source field's LLM-proposed placement on a Uwazi property."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    target: str
    type: str
    required: bool = False
    notes: str | None = None
    default_value: str | None = None
    template: str | None = Field(default=None, description="Template name this draft targets; None = primary template.")
