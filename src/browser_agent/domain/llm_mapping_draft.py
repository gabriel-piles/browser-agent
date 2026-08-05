"""The full LLM draft of a Uwazi mapping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.identity_config import IdentityConfig
from browser_agent.domain.llm_field_draft import LlmFieldDraft


class LlmMappingDraft(BaseModel):
    """The full LLM draft — fields + identity + side flags.

    The identity fields are kept flat (not nested under an
    :class:`IdentityConfig`) so the LLM emits a simple JSON shape.
    :meth:`to_identity` converts them into the canonical
    :class:`IdentityConfig`.
    """

    model_config = ConfigDict(extra="forbid")

    fields: list[LlmFieldDraft] = Field(default_factory=list)
    key_field: str | None = None
    key_property: str | None = None
    select_filtering_name: str | None = None
    select_filtering_options: list[str] = Field(default_factory=list)
    publish: bool = False
    upload_pdf: bool = False
    skipped: list[dict] = Field(default_factory=list)

    def to_identity(self) -> IdentityConfig:
        """Build the canonical :class:`IdentityConfig` from the flat LLM fields."""
        return IdentityConfig(
            key_field=self.key_field,
            key_property=self.key_property,
            select_filtering_name=self.select_filtering_name,
            select_filtering_options=tuple(self.select_filtering_options),
        )
