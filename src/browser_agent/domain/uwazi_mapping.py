"""The full Uwazi mapping: template + properties + identity + flags.

This is the contract between ``browser-agent`` metadata rows and a
specific Uwazi template. The :class:`UwaziMapping` is the only
file ``step_3_uwazi_apply.py`` reads to push data; ``step_1_uwazi_propose.py``
drafts it, a human reviews it, and ``step_2_uwazi_match.py`` adds
per-thesaurus value mappings that the apply step then uses to
normalise select/multiselect columns.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.identity_config import IdentityConfig
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.skipped_field import SkippedField


class UwaziMapping(BaseModel):
    """The full contract between ``browser-agent`` records and a Uwazi template."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=2, description="YAML schema version; bumped on breaking changes.")
    template: str = Field(description="Name of the Uwazi template to push to.")
    default_language: str = Field(default="en", description="ISO language code sent on every create/update.")
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    properties: tuple[MappedProperty, ...] = Field(
        default_factory=tuple,
        description="The single list the operator edits: every target property on Uwazi plus how it is filled.",
    )
    skipped: tuple[SkippedField, ...] = Field(default_factory=tuple)
    publish: bool = Field(default=False, description="Whether to publish created entities (vs leave them as drafts).")
    upload_pdf: bool = Field(default=False, description="Whether to attach the local PDF file when one exists.")

    def property_for_source(self, source_name: str) -> MappedProperty | None:
        """Return the :class:`MappedProperty` whose source is ``source_name``, or None."""
        for prop in self.properties:
            if prop.source == source_name:
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
