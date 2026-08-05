"""Render the propose LLM prompt from the Uwazi template + field catalog.

The :class:`ProposeMappingUseCase` delegates here so the prompt
construction (template snapshot, catalog blob, system text) lives
behind one object instead of a stack of free functions.
"""

from __future__ import annotations

import json

from browser_agent.domain.metadata_field_catalog import MetadataFieldCatalog
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_template import UwaziTemplate

_PROPOSE_SYSTEM_PROMPT = """You are a Uwazi-mapping assistant.
You are given a snapshot of a Uwazi template (its name, id, properties,
its common properties like ``title``, and the thesaurus values for
each select/multiselect property) and a catalog of source fields
scraped from the web. Your job is to draft a mapping that sends every
useful source field to a Uwazi property and guesses default values for
the template properties that have no matching scraped field.

Rules:
- Place every field you can; use ``type="title"`` for the entity title
  (always target the ``title`` common property, never leave it empty),
  ``type="date"`` for dates, ``type="select"``
  / ``type="multiselect"`` for fields backed by a thesaurus (set
  ``thesaurus`` to the thesaurus name), ``type="text"`` for plain
  strings, ``type="numeric"`` for numbers, ``type="markdown"`` for
  long-form text, ``type="link"`` for URL-valued fields, ``type="skipped"``
  for fields you cannot place.
- The ``title`` common property is mandatory. Always emit a
  ``fields`` entry whose ``target`` is the name of the title common
  property (see ``common_properties`` in the template snapshot) and
  whose ``type`` is ``"title"``. Map it to the scraped field whose
  value best identifies one record (e.g. a heading, document title,
  or case name); if no good match exists, pick the most descriptive
  free-text field.
- For the identity block: set ``key_field`` to the source field whose
  value uniquely identifies one record and ``key_property`` to the
  Uwazi property name to match against existing entities. The apply
  pipeline reads ``key_field`` from the record and skips any row whose
  value already exists on Uwazi under ``key_property``. When the
  template has a ``link``-type property, prefer it as ``key_property``
  and the source URL (or a URL-derived field) as ``key_field`` so
  already-uploaded entities are detected by their link. Leave both
  null only when every record should be created unconditionally.
- For the ``select_filtering`` block: when the template has one or
  more ``select`` properties whose value space partitions the
  entities in a way the operator can predict (e.g. a ``status``,
  ``language``, ``country`` select), propose a narrow download of
  existing entities instead of fetching every row. Set
  ``select_filtering_name`` to the Uwazi property name (must match
  one of the template's properties) and ``select_filtering_options``
  to a list of leaf-label values the operator is willing to ingest;
  the apply/match drivers will only download entities whose value on
  that property is in the list. Leave both empty (or ``null``) to
  keep the unfiltered behaviour. The chosen values must come from
  the property's ``thesaurus_values`` leaf labels.
- For every template property that has NO matching scraped field, emit
  a field entry with ``source=null`` and a guessed ``default_value``:
  a constant text, a thesaurus **leaf** label (for select/multiselect),
  an ISO date (``YYYY-MM-DD``), a number string, or ``null`` to leave
  the property unset. Set ``type`` to the property's type. For thesaurus
  fields, pick one of the listed ``thesaurus_values`` leaf labels, **not**
  a parent group name.
- Skip a scraped field only when (a) the catalog marks it
  ``export_to_uwazi=false`` and the operator opted not to push it,
  or (b) the field has no plausible Uwazi property to map to. Each
  skipped field goes in the ``skipped`` list with reason + notes.
- When a second "Uwazi registry template" snapshot is present, you
  must produce property mappings for BOTH templates. Properties that
  share the SAME name in both templates are merged: emit them ONCE in
  ``fields`` (one entry serves both templates). Properties unique to
  the registry template get their own ``fields`` entries with
  ``template`` set to the registry template's name (omit ``template``
  or set it to null for the primary template). Do NOT map a ``title``
  for the registry template — only the primary template's title is
  drafted; the registry entity reuses the primary entity's title at
  upload time. The identity block is shared (same ``key_property`` name
  in both templates) — emit a single identity block.
- For the registry template's ``scraper_date_property`` (if named in
  the user prompt), emit a ``fields`` entry with ``source=null``,
  ``type="date"``, ``default_value=null``, and ``template`` set to
  the registry template's name. It is filled at upload time, not from
  scraped data.
- For the registry template's ``scraper_document_relationship`` (if
  named in the user prompt), emit a ``fields`` entry with
  ``source=null``, ``type="relationship"``, ``default_value=null``,
  and ``template`` set to the registry template's name. It is filled at
  upload time by linking to the created primary entity.
- For the registry template's ``scraper_document_hash`` (if named in
  the user prompt), emit a ``fields`` entry with ``source=null``,
  ``type="text"``, ``default_value=null``, and ``template`` set to
  the registry template's name. It is filled at upload time with the
  SHA-256 hash of the scraped document file (PDF/DOC), or left empty
  when no file exists; never map a scraped field to it.
- Output ONLY the structured JSON matching the schema. Do not output
  prose, explanations, or markdown fences.
"""


class ProposePromptRenderer:
    """Build the system + user prompt for the propose LLM call."""

    SYSTEM_PROMPT = _PROPOSE_SYSTEM_PROMPT

    def user_prompt(
        self,
        template: UwaziTemplate,
        catalog: MetadataFieldCatalog,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        registry_template: UwaziTemplate | None = None,
        scraper_date_property: str | None = None,
        scraper_document_relationship: str | None = None,
        scraper_document_hash: str | None = None,
    ) -> str:
        """Compose the user-turn prompt for the propose Agent call."""
        parts = [
            f"## Uwazi template (snapshot at propose time)\n{self._template_snapshot(template, thesauri_by_id)}\n\n",
        ]
        if registry_template is not None:
            parts.append(
                f"## Uwazi registry template (snapshot)\n{self._template_snapshot(registry_template, thesauri_by_id)}\n\n"
            )
            parts.append(
                f"## Registry-template special properties\n"
                f"scraper_date_property = {scraper_date_property!r} (type=date, source=null, filled at upload time)\n"
                f"scraper_document_relationship = {scraper_document_relationship!r} (type=relationship, source=null, filled at upload time)\n"
                f"scraper_document_hash = {scraper_document_hash!r} (type=text, source=null, filled at upload time with the file SHA-256)\n\n"
            )
        parts.append(
            f"## Source catalog (from metadata.db for run {catalog.run!r})\n"
            f"{self._catalog_blob(catalog)}\n\n"
            "Return the JSON object conforming to the schema. Every catalog "
            "field marked export_to_uwazi=true must be placed on a target "
            "property, the ``title`` common property must always be filled, "
            "and every template property with no matching field must get a "
            "source=null default entry."
        )
        return "".join(parts)

    def _template_snapshot(self, template, thesauri_by_id) -> str:
        """Render the Uwazi template as a JSON blob for the LLM prompt."""
        payload = {
            "name": template.name,
            "template_id": template.template_id,
            "common_properties": [self._template_property(p, thesauri_by_id) for p in self._common_props(template)],
            "properties": [self._template_property(p, thesauri_by_id) for p in template.properties],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _common_props(self, template: UwaziTemplate) -> tuple:
        """Return the common properties to expose to the LLM (currently just title)."""
        return (template.title,) if template.title is not None else ()

    def _template_property(self, prop, thesauri_by_id) -> dict:
        """Return the dict shape of one :class:`UwaziProperty` for the prompt."""
        out: dict = {
            "name": prop.name,
            "label": prop.label,
            "type": prop.type.value,
            "required": prop.required,
            "thesaurus_id": prop.thesaurus_id,
        }
        if prop.thesaurus_id and prop.thesaurus_id in thesauri_by_id:
            out["thesaurus_values"] = list(thesauri_by_id[prop.thesaurus_id].values)
        return out

    def _catalog_blob(self, catalog: MetadataFieldCatalog) -> str:
        """Render the field catalog as a JSON blob for the LLM prompt."""
        payload = {
            "run": catalog.run,
            "pattern": catalog.pattern,
            "cohesion_assessment": catalog.cohesion_assessment,
            "fields": [self._catalog_field(f) for f in catalog.fields],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _catalog_field(self, field) -> dict:
        """Return the dict shape of one :class:`MetadataField` for the prompt."""
        return {
            "name": field.name,
            "description": field.description,
            "value_type": field.value_type,
            "examples": list(field.examples),
            "export_to_uwazi": field.export_to_uwazi,
        }
