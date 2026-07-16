"""One field in the LLM's mapping draft."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LlmFieldDraft(BaseModel):
    """One source field's LLM-proposed placement on a Uwazi property."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    target: str
    type: str
    thesaurus: str | None = None
    parse_formats: list[str] = Field(default_factory=list)
    required: bool = False
    notes: str | None = None
    default_value: str | None = None
