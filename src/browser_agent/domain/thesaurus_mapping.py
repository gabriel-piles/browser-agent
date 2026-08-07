"""One thesaurus mapping: one Uwazi property + every value entry for it.

Written by ``step_4_validate_data.py`` as
``data/runs/<run>/thesauri_mappings/<property_name>.yaml`` (one file
per Uwazi property, so two properties sharing a thesaurus get two
files); read by ``step_5_upload_to_uwazi.py`` to substitute crawl
values with their canonical Uwazi thesaurus values before pushing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusMapping(BaseModel):
    """The full mapping for one Uwazi property."""

    model_config = ConfigDict(extra="forbid")

    property_name: str = Field(description="Uwazi property this mapping feeds; also the YAML file name.")
    thesaurus: str = Field(description="Thesaurus name backing the property (audit reference).")
    thesaurus_id: str = Field(
        description=(
            "Uwazi internal thesaurus id on the instance that generated the mapping; "
            "informational only — the apply pipeline resolves substitution by property "
            "name so the file works on any instance."
        )
    )
    uwazi_name: str = Field(description="Canonical Uwazi thesaurus name (may differ from ``thesaurus`` if renamed).")
    default_language: str = Field(default="en")
    generated_by: str = Field(default="", description="LLM model that produced this mapping, for audit.")
    entries: tuple[ThesaurusMappingEntry, ...] = Field(default_factory=tuple)
