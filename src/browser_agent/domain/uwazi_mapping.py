"""The full Uwazi mapping: template + properties + identity + flags.

This is the contract between ``browser-agent`` metadata rows and a
specific Uwazi template. The :class:`UwaziMapping` is the only
file ``step_5_upload_to_uwazi.py`` reads to push data; ``step_3_propose_mapping.py``
drafts it, a human reviews it, and ``step_4_validate_data.py`` adds
per-thesaurus value mappings that the apply step then uses to
normalise select/multiselect columns.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.identity_config import IdentityConfig
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.metadata_coverage import MetadataCoverage
from browser_agent.domain.skipped_field import SkippedField


class UwaziMapping(BaseModel):
    """The full contract between ``browser-agent`` records and a Uwazi template."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=3, description="YAML schema version; bumped on breaking changes.")
    template: str = Field(description="Name of the Uwazi template to push to.")
    default_language: str = Field(default="en", description="ISO language code sent on every create/update.")
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    skipped: tuple[SkippedField, ...] = Field(default_factory=tuple, description="Catalog fields the LLM declined to map.")
    metadata_stats: MetadataCoverage | None = Field(
        default=None,
        description=(
            "Optional snapshot of the metadata.db coverage (total entities + per-field counts) "
            "recorded by step_3 when the mapping was drafted. Older or hand-written mappings "
            "omit it; the apply and validate steps do not read it."
        ),
    )

    publish: bool = Field(default=False, description="Whether to publish created entities (vs leave them as drafts).")
    upload_pdf: bool = Field(default=False, description="Whether to attach the local PDF file when one exists.")
    registry_template: str | None = Field(
        default=None, description="Name of the scraper-registry Uwazi template; None disables the registry flow."
    )
    scraper_date_property: str | None = Field(
        default=None, description="Registry-template property to receive the entity creation date."
    )
    scraper_document_relationship: str | None = Field(
        default=None, description="Registry-template relationship property linking to the primary template entity."
    )
    scraper_document_hash: str | None = Field(
        default=None,
        description="Registry-template property to receive the SHA-256 hash of the scraped document file at upload time.",
    )
    properties: tuple[MappedProperty, ...] = Field(
        default_factory=tuple,
        description=(
            "The single list the operator edits: every target property on Uwazi plus how it is filled. "
            "The first entry (when the template declares a title) is the ``title`` common property "
            "(type=TITLE); the apply step reads it as ``Entity.title`` and the metadata builder skips it."
        ),
    )

    def property_for_source(self, source_name: str) -> MappedProperty | None:
        """Return the :class:`MappedProperty` whose source is ``source_name``, or None."""
        for prop in self.properties:
            if prop.source and source_name in prop.source:
                return prop
        return None

    def file_property(self) -> MappedProperty | None:
        """Return the :class:`MappedProperty` declared as a FILE property, if any."""
        for prop in self.properties:
            if prop.type.value == "file":
                return prop
        return None

    def title_property(self) -> MappedProperty | None:
        """Return the :class:`MappedProperty` declared as the TITLE property, if any."""
        for prop in self.properties:
            if prop.type.value == "title":
                return prop
        return None

    @property
    def sha256(self) -> str:
        """Stable hash of the mapping body, used to fingerprint plans.

        Two mappings with the same SHA are treated as equivalent; an
        edit to any property produces a different SHA so the apply
        driver can detect drift. ``upload_pdf`` is included because
        toggling it changes what gets uploaded to Uwazi.
        """
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
