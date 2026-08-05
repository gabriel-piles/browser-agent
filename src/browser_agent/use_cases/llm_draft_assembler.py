"""Convert a validated :class:`LlmMappingDraft` into a :class:`UwaziMapping`.

The :class:`ProposeMappingUseCase` delegates the coercion of the
LLM's draft (string-typed field types, raw skipped dicts) into the
canonical pydantic domain models here.

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

    def assemble(
        self,
        draft: LlmMappingDraft,
        template: UwaziTemplate,
        registry_template: UwaziTemplate | None = None,
        scraper_date_property: str | None = None,
        scraper_document_relationship: str | None = None,
        scraper_document_hash: str | None = None,
    ) -> UwaziMapping:
        """Build the :class:`UwaziMapping` from ``draft`` + ``template`` (+ optional registry).

        Domain entries are ordered so the properties the LLM matched to a
        scraped source come first and the source-less (ignored) ones last;
        the title entry, when present, stays pinned at the front.

        When ``registry_template`` is set, properties shared by name
        between the primary and registry templates carry BOTH template
        names in ``template_name`` so the apply pipeline maps them for
        both at once; registry-only properties get their own entries
        tagged with only the registry name. The registry template's
        title is ignored (the registry entity reuses the primary title).
        """
        by_target = {raw.target: raw for raw in draft.fields}
        title_entry = self._title_entry(template, by_target)
        shared_names = self._shared_names(template, registry_template)
        domain_entries = tuple(
            MappedProperty.from_template_and_draft(
                p,
                by_target.get(p.name),
                template_name=self._names_for(p.name, template.name, registry_template, shared_names),
            )
            for p in template.properties
        )
        all_entries = (title_entry,) + domain_entries if title_entry is not None else domain_entries
        if registry_template is not None:
            all_entries = all_entries + self._registry_only_entries(registry_template, draft, shared_names)
        properties = MappedProperty.order_by_match(all_entries)
        return UwaziMapping(
            template=template.name,
            default_language=template.default_language,
            identity=draft.to_identity(),
            properties=properties,
            skipped=self._skipped(draft),
            publish=draft.publish,
            upload_pdf=draft.upload_pdf,
            registry_template=registry_template.name if registry_template is not None else None,
            scraper_date_property=scraper_date_property,
            scraper_document_relationship=scraper_document_relationship,
            scraper_document_hash=scraper_document_hash,
        )

    @staticmethod
    def _shared_names(template: UwaziTemplate, registry_template: UwaziTemplate | None) -> set[str]:
        """Return property names present in both the primary and registry templates."""
        if registry_template is None:
            return set()
        primary = {p.name for p in template.properties}
        if template.title is not None:
            primary.add(template.title.name)
        registry = {p.name for p in registry_template.properties}
        return primary & registry

    @staticmethod
    def _names_for(
        name: str,
        primary: str,
        registry_template: UwaziTemplate | None,
        shared_names: set[str],
    ) -> tuple[str, ...]:
        """Return the ``template_name`` tuple for one primary property."""
        if registry_template is not None and name in shared_names:
            return (primary, registry_template.name)
        return (primary,)

    def _registry_only_entries(
        self,
        registry_template: UwaziTemplate,
        draft: LlmMappingDraft,
        shared_names: set[str],
    ) -> tuple[MappedProperty, ...]:
        """Return registry properties NOT shared with the primary template.

        Properties whose name exists in the primary template are shared
        (already mapped with both names); the rest are registry-only and
        tagged with only the registry template name.
        """
        registry_drafts = {raw.target: raw for raw in draft.fields if raw.template == registry_template.name}
        entries: list[MappedProperty] = []
        for prop in registry_template.properties:
            if prop.name in shared_names:
                continue
            draft_for_prop = registry_drafts.get(prop.name)
            entry = MappedProperty.from_template_and_draft(prop, draft_for_prop, template_name=(registry_template.name,))
            entries.append(entry)
        return tuple(entries)

    def _title_entry(self, template: UwaziTemplate, by_target: dict) -> MappedProperty | None:
        """Build the title :class:`MappedProperty`, or None when the template has no title."""
        title_prop = template.title
        if title_prop is None:
            return None
        return MappedProperty.title_from_draft(title_prop, by_target.get(title_prop.name), template_name=(template.name,))

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
