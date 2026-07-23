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
        domain_entries = tuple(MappedProperty.from_template_and_draft(p, by_target.get(p.name)) for p in template.properties)
        properties = (title_entry,) + domain_entries if title_entry is not None else domain_entries
        return UwaziMapping(
            template=template.name,
            default_language=template.default_language,
            identity=draft.to_identity(),
            properties=properties,
            skipped=self._skipped(draft),
            publish=draft.publish,
            upload_pdf=draft.upload_pdf,
        )

    def _title_entry(self, template: UwaziTemplate, by_target: dict) -> MappedProperty | None:
        """Build the title :class:`MappedProperty`, or None when the template has no title."""
        title_prop = template.title
        if title_prop is None:
            return None
        return MappedProperty.title_from_draft(title_prop, by_target.get(title_prop.name))

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
