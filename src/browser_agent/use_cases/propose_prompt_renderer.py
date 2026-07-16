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
and the thesaurus values for each select/multiselect property) and a
catalog of source fields scraped from the web. Your job is to draft a
mapping that sends every useful source field to a Uwazi property and
guesses default values for the template properties that have no
matching scraped field.

Rules:
- Place every field you can; use ``type="title"`` for the entity title,
  ``type="date"`` for dates (with ``parse_formats``), ``type="select"``
  / ``type="multiselect"`` for fields backed by a thesaurus (set
  ``thesaurus`` to the thesaurus name), ``type="text"`` for plain
  strings, ``type="numeric"`` for numbers, ``type="markdown"`` for
  long-form text, ``type="link"`` for URL-valued fields, ``type="skipped"``
  for fields you cannot place.
- For the identity block: pick the simplest key source that uniquely
  identifies a record. Prefer ``path_placeholder`` when the source
  URLs share a stable pattern; otherwise use ``field``. When the
  template has a ``link``-type property, prefer
  ``key_source="key_field_and_property"`` with that property as
  ``key_property`` and the source URL (or a URL-derived field) as
  ``key_field`` so already-uploaded entities are detected by their link.
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
    ) -> str:
        """Compose the user-turn prompt for the propose Agent call."""
        return (
            f"## Uwazi template (snapshot at propose time)\n"
            f"{self._template_snapshot(template, thesauri_by_id)}\n\n"
            f"## Source catalog (from metadata.db for run {catalog.run!r})\n"
            f"{self._catalog_blob(catalog)}\n\n"
            "Return the JSON object conforming to the schema. Every catalog "
            "field marked export_to_uwazi=true must be placed on a target "
            "property, and every template property with no matching field "
            "must get a source=null default entry."
        )

    def _template_snapshot(self, template, thesauri_by_id) -> str:
        """Render the Uwazi template as a JSON blob for the LLM prompt."""
        payload = {
            "name": template.name,
            "template_id": template.template_id,
            "properties": [self._template_property(p, thesauri_by_id) for p in template.properties],
        }
        return json.dumps(payload, ensure_ascii=False)

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
