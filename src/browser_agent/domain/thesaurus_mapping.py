"""One thesaurus mapping: thesaurus id + every entry for it.

Written by ``step_2_validate_data.py`` to ``data/runs/<run>/thesauri_mappings/<name>.yaml``;
read by ``step_3_upload_to_uwazi.py`` to substitute crawl values with their
canonical Uwazi thesaurus values before pushing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusMapping(BaseModel):
    """The full mapping for one Uwazi thesaurus."""

    model_config = ConfigDict(extra="forbid")

    thesaurus: str = Field(description="Thesaurus name; matches the mapping's ``FieldMapping.thesaurus``.")
    thesaurus_id: str = Field(description="Uwazi internal thesaurus id.")
    uwazi_name: str = Field(description="Canonical Uwazi thesaurus name (may differ from ``thesaurus`` if renamed).")
    default_language: str = Field(default="en")
    generated_by: str = Field(default="", description="LLM model that produced this mapping, for audit.")
    entries: tuple[ThesaurusMappingEntry, ...] = Field(default_factory=tuple)
