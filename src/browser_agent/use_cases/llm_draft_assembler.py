"""Convert a validated :class:`LlmMappingDraft` into a :class:`UwaziMapping`.

The :class:`ProposeMappingUseCase` delegates the coercion of the
LLM's draft (string-typed field types, raw skipped dicts, string
key_source) into the canonical pydantic domain models here.

The output mapping has a single ``properties`` list: each entry is a
Uwazi template property enriched with the source/default choices the
LLM proposed. The first entry is the entity title when the template
declares one — the apply step reads it back as ``Entity.title`` and
the metadata builder skips it (title is not part of the Uwazi
``metadata`` blob, it lives on the entity itself).
"""

from __future__ import annotations

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.identity_config import IdentityConfig, KeySource
from browser_agent.domain.llm_mapping_draft import LlmMappingDraft
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.skipped_field import SkippedField
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate


class LlmDraftAssembler:
    """Turn one :class:`LlmMappingDraft` into a canonical :class:`UwaziMapping`."""

    def assemble(self, draft: LlmMappingDraft, template: UwaziTemplate) -> UwaziMapping:
        """Build the :class:`UwaziMapping` from ``draft`` + ``template``."""
        by_target = {raw.target: raw for raw in draft.fields}
        title_entry = self._title_entry(template, by_target)
        domain_entries = tuple(self._mapped_property(prop, by_target.get(prop.name)) for prop in template.properties)
        properties = (title_entry,) + domain_entries if title_entry is not None else domain_entries
        return UwaziMapping(
            template=template.name,
            default_language=template.default_language,
            identity=self._identity(draft),
            properties=properties,
            skipped=self._skipped(draft),
            publish=draft.publish,
            upload_pdf=draft.upload_pdf,
        )

    def _title_entry(
        self,
        template: UwaziTemplate,
        by_target: dict,
    ) -> MappedProperty | None:
        """Build the title :class:`MappedProperty` for ``mapping.properties``.

        Returns ``None`` when the template has no title common property.
        The returned entry has :attr:`FieldType.TITLE` and the
        source/default the LLM picked; the apply step consumes it as
        ``Entity.title`` (the Uwazi metadata blob does NOT carry the
        title).
        """
        title_prop = template.title
        if title_prop is None:
            return None
        draft = by_target.get(title_prop.name)
        return MappedProperty(
            name=title_prop.name,
            label=title_prop.label,
            type=FieldType.TITLE,
            required=title_prop.required,
            thesaurus_id=None,
            source=draft.source if draft is not None else None,
            thesaurus=None,
            parse_formats=tuple(draft.parse_formats or ()) if draft is not None else (),
            default_value=draft.default_value if draft is not None else None,
            notes=draft.notes if draft is not None else None,
        )

    def _mapped_property(
        self,
        template_prop,
        draft,
    ) -> MappedProperty:
        """Merge one live template property with the LLM draft, if any."""
        return MappedProperty(
            name=template_prop.name,
            label=template_prop.label,
            type=template_prop.type,
            required=template_prop.required,
            thesaurus_id=template_prop.thesaurus_id,
            source=draft.source if draft is not None else None,
            thesaurus=draft.thesaurus if draft is not None else None,
            parse_formats=tuple(draft.parse_formats or ()) if draft is not None else (),
            default_value=draft.default_value if draft is not None else None,
            notes=draft.notes if draft is not None else None,
        )

    def _skipped(self, draft: LlmMappingDraft) -> tuple[SkippedField, ...]:
        """Coerce every LLM-emitted skipped dict into a :class:`SkippedField`."""
        return tuple(self._skipped_field(raw) for raw in draft.skipped)

    def _skipped_field(self, raw: dict) -> SkippedField:
        """Coerce a single LLM-emitted skipped dict into a :class:`SkippedField`."""
        return SkippedField(
            source=str(raw.get("source", "")),
            reason=str(raw.get("reason", "no_match")),
            notes=(str(raw.get("notes")) if raw.get("notes") is not None else None),
        )

    def _identity(self, draft: LlmMappingDraft) -> IdentityConfig:
        """Build an :class:`IdentityConfig` from the LLM draft."""
        return IdentityConfig(
            key_source=self._key_source(draft.key_source),
            key_field=draft.key_field,
            key_property=draft.key_property,
            path_placeholder=draft.path_placeholder,
            source_url_property=draft.source_url_property,
            select_filtering_name=draft.select_filtering_name,
            select_filtering_options=tuple(draft.select_filtering_options),
        )

    def _key_source(self, raw: str | None) -> KeySource:
        """Map an LLM-emitted key_source string to a :class:`KeySource`."""
        if not raw:
            return KeySource.PATH_PLACEHOLDER
        try:
            return KeySource(raw)
        except ValueError:
            return KeySource.PATH_PLACEHOLDER
