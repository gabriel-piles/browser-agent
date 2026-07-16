"""Apply post-LLM fallbacks to a drafted :class:`UwaziMapping`.

The :class:`ProposeMappingUseCase` delegates here to patch the
identity placeholder, default the link-key property, and append
``source=None`` default entries for template properties the LLM
left uncovered. The filler receives the live Uwazi template so it
can synthesise the missing entries from real template metadata.
"""

from __future__ import annotations

import re

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.identity_config import KeySource
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.metadata_field_catalog import MetadataFieldCatalog
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_property import UwaziProperty
from browser_agent.domain.uwazi_template import UwaziTemplate

_URL_PATTERN_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][A-Za-z0-9_]*>")


class MappingFallbackFiller:
    """Patch a drafted :class:`UwaziMapping` with sensible defaults."""

    def apply(
        self,
        mapping: UwaziMapping,
        catalog: MetadataFieldCatalog,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> None:
        """Patch the identity (frozen-safe) and append missing default entries."""
        self._patch_identity(mapping, catalog, template)
        self._fill_missing_defaults(mapping, template, thesauri_by_id or {})
        self._correct_default_values(mapping, thesauri_by_id or {})

    def _patch_identity(
        self,
        mapping: UwaziMapping,
        catalog: MetadataFieldCatalog,
        template: UwaziTemplate,
    ) -> None:
        """Replace the frozen identity with placeholder + link-key defaults filled."""
        updates: dict = {}
        if mapping.identity.path_placeholder is None:
            updates["path_placeholder"] = self._placeholder(catalog.pattern)
        if mapping.identity.key_source is KeySource.KEY_FIELD_AND_PROPERTY and not mapping.identity.key_property:
            link_prop = self._first_link_property(template)
            if link_prop is not None:
                updates["key_property"] = link_prop.name
        if updates:
            mapping.identity = mapping.identity.model_copy(update=updates)

    def _fill_missing_defaults(
        self,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> None:
        """Append ``source=None`` entries for template properties the LLM missed."""
        missing = [p for p in template.properties if p.name not in self._mapped_targets(mapping)]
        if not missing:
            return
        mapping.properties = mapping.properties + tuple(self._default_entry(p, thesauri_by_id) for p in missing)

    def _correct_default_values(
        self,
        mapping: UwaziMapping,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> None:
        """Fix select/multiselect default values that are parent group names."""
        updated: list[MappedProperty] = []
        for prop in mapping.properties:
            if prop.source is not None or prop.default_value is None:
                updated.append(prop)
                continue
            if prop.type not in (FieldType.SELECT, FieldType.MULTI_SELECT) or not prop.thesaurus_id:
                updated.append(prop)
                continue
            thesaurus = thesauri_by_id.get(prop.thesaurus_id)
            if thesaurus is None:
                updated.append(prop)
                continue
            corrected = self._correct_default_value(prop.default_value, thesaurus)
            if corrected != prop.default_value:
                prop = prop.model_copy(
                    update={
                        "default_value": corrected,
                        "notes": self._amended_note(prop, corrected),
                    }
                )
            updated.append(prop)
        mapping.properties = tuple(updated)

    def _correct_default_value(
        self,
        default_value: str,
        thesaurus: ThesauriSnapshot,
    ) -> str:
        """Return a leaf label; if ``default_value`` is a parent group, pick its first leaf."""
        leaves = set(thesaurus.values)
        if default_value in leaves:
            return default_value
        for node in thesaurus.tree:
            if node.label == default_value and node.values:
                return node.values[0].label
        return default_value

    def _amended_note(self, prop: MappedProperty, corrected: str) -> str | None:
        """Append a correction note to the existing notes."""
        base = prop.notes or ""
        suffix = f"default_value corrected from {prop.default_value!r} to leaf label {corrected!r}"
        if base:
            return f"{base}; {suffix}"
        return suffix

    def _mapped_targets(self, mapping: UwaziMapping) -> set[str]:
        """Return the Uwazi property names already covered by the mapping."""
        return {p.name for p in mapping.properties}

    def _default_entry(
        self,
        prop: UwaziProperty,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> MappedProperty:
        """Build a ``source=None`` :class:`MappedProperty` placeholder for one property."""
        default_value = self._default_value_for(prop, thesauri_by_id)
        return MappedProperty(
            name=prop.name,
            label=prop.label,
            type=prop.type,
            required=prop.required,
            thesaurus_id=prop.thesaurus_id,
            source=None,
            thesaurus=None,
            default_value=default_value,
            notes="unmapped template property; no matching scraped field",
        )

    def _default_value_for(
        self,
        prop: UwaziProperty,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> str | None:
        """Return a sensible default for ``prop``, or None when not required."""
        if not prop.required:
            return None
        if prop.type in (FieldType.SELECT, FieldType.MULTI_SELECT) and prop.thesaurus_id:
            thesaurus = thesauri_by_id.get(prop.thesaurus_id)
            if thesaurus and thesaurus.values:
                return thesaurus.values[0]
        return None

    def _first_link_property(self, template: UwaziTemplate) -> UwaziProperty | None:
        """Return the first ``link``-type property on the template, or None."""
        for prop in template.properties:
            if prop.type is FieldType.LINK:
                return prop
        return None

    def _placeholder(self, pattern: str) -> str | None:
        """Extract the first ``<name>`` placeholder from a URL pattern, or None."""
        match = _URL_PATTERN_PLACEHOLDER_RE.search(pattern)
        return match.group(0).strip("<>") if match else None
