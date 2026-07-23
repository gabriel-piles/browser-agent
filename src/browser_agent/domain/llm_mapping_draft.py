"""The full LLM draft of a Uwazi mapping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.identity_config import IdentityConfig, KeySource
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
    key_source: str | None = None
    key_field: str | None = None
    key_property: str | None = None
    path_placeholder: str | None = None
    source_url_property: str | None = None
    select_filtering_name: str | None = None
    select_filtering_options: list[str] = Field(default_factory=list)
    publish: bool = False
    upload_pdf: bool = False
    skipped: list[dict] = Field(default_factory=list)

    def to_identity(self) -> IdentityConfig:
        """Build the canonical :class:`IdentityConfig` from the flat LLM fields."""
        return IdentityConfig(
            key_source=self._key_source(),
            key_field=self.key_field,
            key_property=self.key_property,
            path_placeholder=self.path_placeholder,
            source_url_property=self.source_url_property,
            select_filtering_name=self.select_filtering_name,
            select_filtering_options=tuple(self.select_filtering_options),
        )

    def _key_source(self) -> KeySource:
        """Map the raw key_source string to a :class:`KeySource` enum."""
        if not self.key_source:
            return KeySource.PATH_PLACEHOLDER
        try:
            return KeySource(self.key_source)
        except ValueError:
            return KeySource.PATH_PLACEHOLDER
