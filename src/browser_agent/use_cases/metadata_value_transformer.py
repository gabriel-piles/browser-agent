"""Transform raw metadata.db rows into the Uwazi ``Entity.metadata`` shape.

The apply pipeline and the upload-validation report both need the
same per-property transformations: thesaurus value substitution, date
parsing, select/multiselect wrapping, link wrapping. Centralising them
in one class keeps the two callers in sync and moves the
type-dispatch logic off the free-function stack in
:mod:`apply_mapping_use_case`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import yaml

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.use_cases.source_value_resolver import resolve_source_value
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from uwazi_api.domain.thesauri_value import ThesauriValue


_THESAURUS_TYPES = (FieldType.SELECT, FieldType.MULTI_SELECT, FieldType.RELATIONSHIP)
_DEFAULT_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y")


def build_metadata_for_row(
    record: dict,
    source_url: str,
    mapping: UwaziMapping,
    thesaurus_lookup_by_property: dict[str, dict[str, str | None]],
    thesaurus_parents: dict[str, dict[str, str | None]] | None = None,
    relationship_title_to_id: dict[str, dict[str, str]] | None = None,
    template: UwaziTemplate | None = None,
) -> dict:
    """Build the post-transform metadata dict for one record (module-level convenience)."""
    return MetadataValueTransformer(template=template).build_for_row(
        record, source_url, mapping, thesaurus_lookup_by_property, thesaurus_parents, relationship_title_to_id
    )


class MetadataValueTransformer:
    """Apply per-property value transformations for the Uwazi metadata blob."""

    def __init__(self, template: UwaziTemplate | None = None) -> None:
        """Store the live ``template`` used to resolve thesaurus ids on the fly."""
        self._template = template

    def build_for_row(
        self,
        record: dict,
        source_url: str,
        mapping: UwaziMapping,
        thesaurus_lookup_by_property: dict[str, dict[str, str | None]],
        thesaurus_parents: dict[str, dict[str, str | None]] | None = None,
        relationship_title_to_id: dict[str, dict[str, str]] | None = None,
    ) -> dict:
        """Build the primary-template metadata dict for one record.

        Title and file properties are skipped (stored on the entity, not in
        ``metadata``). When a registry template is set, properties that belong
        only to the registry template are skipped here; shared properties
        (also on the primary template) are kept. Public so the upload-validation
        report can reuse the substitution logic.
        """
        out: dict = {}
        for prop in mapping.properties:
            if prop.type in (FieldType.TITLE, FieldType.SKIPPED, FieldType.FILE):
                continue
            if (
                mapping.registry_template
                and mapping.registry_template in prop.template_name
                and not (
                    (not prop.template_name or mapping.template in prop.template_name)
                    and (self._template is None or self._template.property_by_name(prop.name) is not None)
                )
            ):
                continue
            out[prop.name] = self._property_value(
                record,
                prop,
                source_url,
                thesaurus_parents,
                relationship_title_to_id,
                thesaurus_lookup_by_property,
            )
        return {k: v for k, v in out.items() if v is not None}

    def build_registry_metadata_for_row(
        self,
        record: dict,
        source_url: str,
        mapping: UwaziMapping,
        thesaurus_lookup_by_property: dict[str, dict[str, str | None]],
        thesaurus_parents: dict[str, dict[str, str | None]] | None = None,
        relationship_title_to_id: dict[str, dict[str, str]] | None = None,
        registry_template: UwaziTemplate | None = None,
    ) -> dict:
        """Build the registry-template metadata dict for one record.

        Includes shared properties (template_name empty or containing the
        primary template name) plus registry-only properties
        (template_name containing the registry name). Excludes the
        ``scraper_date_property``, ``scraper_document_relationship`` and
        ``scraper_document_hash`` which are filled at push time.
        """
        registry_name = mapping.registry_template
        skip_names = {
            mapping.scraper_date_property,
            mapping.scraper_document_relationship,
            mapping.scraper_document_hash,
        }
        out: dict = {}
        for prop in mapping.properties:
            if prop.type in (FieldType.TITLE, FieldType.SKIPPED, FieldType.FILE):
                continue
            if prop.name in skip_names:
                continue
            is_shared = (not prop.template_name or mapping.template in prop.template_name) and (
                self._template is None or self._template.property_by_name(prop.name) is not None
            )
            is_registry = registry_name in prop.template_name
            if not (is_shared or is_registry):
                continue
            out[prop.name] = self._property_value(
                record,
                prop,
                source_url,
                thesaurus_parents,
                relationship_title_to_id,
                thesaurus_lookup_by_property,
            )
        return {k: v for k, v in out.items() if v is not None}

    def _relationship_map_for(self, prop, relationship_title_to_id) -> dict[str, str] | None:
        """Return the title->id map for ``prop`` using the live template's thesaurus_id."""
        if not relationship_title_to_id or self._template is None:
            return None
        tprop = self._template.property_by_name(prop.name)
        if tprop is None or tprop.thesaurus_id is None:
            return None
        return relationship_title_to_id.get(tprop.thesaurus_id)

    def _thesaurus_lookup_for(self, prop, thesaurus_lookup_by_property) -> dict[str, str | None] | None:
        """Return the substitution map for ``prop`` keyed by the Uwazi property name.

        ``step_4_validate_data.py`` writes one ``thesauri_mappings/*.yaml``
        per Uwazi property (named after it, carrying a ``property_name``
        field), so two properties sharing a thesaurus stay separate. The
        lookup is keyed by ``prop.name``; the live template only confirms
        the property is thesaurus-backed on the target instance, so the
        same mapping works against any instance even when the internal
        thesaurus ids differ.
        """
        if self._template is None:
            return None
        tprop = self._template.property_by_name(prop.name)
        if tprop is None or tprop.thesaurus_id is None:
            return None
        if not thesaurus_lookup_by_property:
            return None
        return thesaurus_lookup_by_property.get(prop.name)

    def _property_value(
        self,
        record,
        prop,
        source_url,
        thesaurus_parents,
        relationship_title_to_id,
        thesaurus_lookup_by_property=None,
    ) -> object:
        """Return the coerced value for one property, or None to skip it."""
        lookup = self._thesaurus_lookup_for(prop, thesaurus_lookup_by_property)
        rel_map = self._relationship_map_for(prop, relationship_title_to_id)
        if prop.source is None:
            value = self._default_value(prop)
            if value is None:
                return None
            return self._coerce(value, prop, thesaurus_parents, rel_map)
        raw = resolve_source_value(record, prop.source)
        if raw is None:
            return None
        value = self._format_field(raw, prop, lookup)
        return self._coerce(value, prop, thesaurus_parents, rel_map)

    def _format_field(self, raw, prop: MappedProperty, lookup) -> object:
        """Apply type-specific transformation to one property's raw value."""
        if prop.type is FieldType.DATE:
            return self._coerce_date(raw)
        if prop.type in _THESAURUS_TYPES:
            return self._substitute_thesaurus(raw, prop.type, lookup)
        return raw

    def _default_value(self, prop: MappedProperty) -> object:
        """Return the constant ``default_value`` for a source=None entry.

        Defaults are operator-curated canonical Uwazi values, not crawl
        values, so they bypass thesaurus substitution and date parsing.
        """
        return prop.default_value

    def _coerce(self, value, prop, thesaurus_parents, relationship_title_to_id) -> object:
        """Coerce a transformed value into the shape Uwazi's metadata expects."""
        if value is None or value == "":
            return None
        if prop.type is FieldType.RELATIONSHIP:
            return self._coerce_relationship(value, relationship_title_to_id)
        if prop.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            parents_map = thesaurus_parents.get(prop.name) if thesaurus_parents else None
            return self._wrap_select_value(value, parents_map)
        if prop.type is FieldType.LINK:
            text = str(value)
            return self._link_value(text, text)
        if prop.type is FieldType.TEXT and not isinstance(value, str):
            return str(value)
        return value

    def _coerce_relationship(self, value, relationship_title_to_id) -> object:
        """Resolve relationship titles to entity ids and wrap as ``[{value}]``."""
        if relationship_title_to_id is None:
            return value if isinstance(value, list) else [{"value": value}] if value else None
        if isinstance(value, list):
            return [{"value": relationship_title_to_id.get(v, v)} for v in value if v is not None]
        return [{"value": relationship_title_to_id.get(value, value)}]

    def _coerce_date(self, value) -> str | None:
        """Parse a date string with the first matching default format, or pass it through."""
        if value is None or value == "":
            return None
        text = str(value).strip()
        for fmt in _DEFAULT_DATE_FORMATS:
            parsed = self._try_parse_date(text, fmt)
            if parsed is not None:
                return parsed
        return text

    @staticmethod
    def _try_parse_date(text: str, fmt: str) -> str | None:
        """Return the ISO date string for ``text`` parsed with ``fmt``, or None."""
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            return None

    def _substitute_thesaurus(self, value, field_type: FieldType, lookup: dict[str, str] | None) -> object:
        """Substitute a crawl value with its canonical thesaurus form, dropping unmapped values."""
        if field_type not in _THESAURUS_TYPES:
            return value
        if not lookup:
            return None if not isinstance(value, list) else []
        if isinstance(value, list):
            return [self._substitute_one(item, lookup) for item in value]
        return self._substitute_one(value, lookup)

    @staticmethod
    def _substitute_one(value, lookup: dict[str, str]) -> object:
        """Apply a single thesaurus lookup to one value, dropping unmapped values."""
        if value is None:
            return None
        text = str(value).strip()
        return lookup.get(text)

    def _wrap_select_value(self, value, parents_map: dict[str, str | None] | None) -> list[dict] | None:
        """Wrap a select/multiselect value (scalar or list) as a list of items."""
        if value is None or value == "":
            return None
        if isinstance(value, list):
            items = [self._wrap_select_item(str(v), parents_map) for v in value if v is not None and v != ""]
            return items or None
        return [self._wrap_select_item(str(value), parents_map)]

    @staticmethod
    def _wrap_select_item(label: str, parents_map: dict[str, str | None] | None) -> dict:
        """Wrap one select/multiselect label as the Uwazi ``{value, parent?}`` item."""
        item: dict = {"value": label}
        if parents_map and label in parents_map:
            parent_label = parents_map[label]
            if parent_label is not None:
                item["parent"] = {"label": parent_label}
        return item

    @staticmethod
    def _link_value(label: str, url: str) -> dict:
        """Build the ``{label, url}`` dict the Uwazi link property expects."""
        return {"label": label, "url": url}

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        """Return True when ``value`` is a parseable absolute URL with a non-empty host."""
        try:
            parsed = urlparse(value)
        except (TypeError, ValueError):
            return False
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def build_thesaurus_parents(client, language: str, thesaurus_ids: Iterable[str]) -> dict[str, dict[str, str | None]]:
    """Return ``{thesaurus_id: {label: parent_label_or_None}}`` for ``thesaurus_ids``."""

    wanted = set(thesaurus_ids)
    if not wanted:
        return {}
    out: dict[str, dict[str, str | None]] = {}
    for thesaurus in client.thesauris.get(language):
        if thesaurus.id not in wanted:
            continue
        parents: dict[str, str | None] = {}
        _walk_parents(thesaurus.values, parents)
        out[thesaurus.id] = parents
    return out


def _walk_parents(values: Iterable[ThesauriValue], out: dict[str, str | None], parent_label: str | None = None) -> None:
    """Walk a thesaurus tree and fill ``out`` with each label's parent label (or None)."""
    for v in values:
        out[v.label] = parent_label
        if v.values:
            _walk_parents(v.values, out, parent_label=v.label)


def load_thesauri_mappings_by_property(thesauri_dir: Path) -> dict[str, dict[str, str | None]]:
    """Read every ``thesauri_mappings/*.yaml`` into a property_name -> {crawl: uwazi} dict.

    Step 4 writes one file per Uwazi property, named after it and
    carrying a ``property_name`` field; keying by that field (not the
    file stem) keeps two properties that share a thesaurus separate
    and survives a human renaming the file. The instance-specific
    ``thesaurus_id`` plays no part in the lookup.
    """
    out: dict[str, dict[str, str | None]] = {}
    if not thesauri_dir.is_dir():
        return out
    for path in sorted(thesauri_dir.glob("*.yaml")):
        data = _thesauri_dict_from_yaml(path)
        if not data:
            continue
        property_name = _property_name_from_yaml(path)
        if property_name:
            out[property_name] = data
    return out


def _property_name_from_yaml(path: Path) -> str | None:
    """Return the ``property_name`` field of one mapping YAML, or None."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    property_name = data.get("property_name")
    return property_name if isinstance(property_name, str) else None


def _thesauri_dict_from_yaml(path: Path) -> dict[str, str | None]:
    """Load a single ``thesauri_mappings/*.yaml`` into a crawl -> uwazi dict."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("entries") or ()
    return {
        e["crawl_value"]: e["uwazi_value"]
        for e in entries
        if isinstance(e, dict) and "crawl_value" in e and "uwazi_value" in e
    }
