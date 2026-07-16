"""The full LLM draft of a Uwazi mapping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.llm_field_draft import LlmFieldDraft


class LlmMappingDraft(BaseModel):
    """The full LLM draft — fields + identity + side flags."""

    model_config = ConfigDict(extra="forbid")

    fields: list[LlmFieldDraft] = Field(default_factory=list)
    key_source: str | None = None
    key_field: str | None = None
    key_property: str | None = None
    path_placeholder: str | None = None
    source_url_property: str | None = None
    publish: bool = False
    upload_pdf: bool = False
    skipped: list[dict] = Field(default_factory=list)
