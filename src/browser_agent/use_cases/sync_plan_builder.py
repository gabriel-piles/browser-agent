"""Build a :class:`SyncPlan` from a :class:`UwaziMapping` + ``metadata.db`` rows.

Pure data: no LLM, no side effects on Uwazi. Reads the metadata rows,
applies thesaurus substitution / date parsing / select wrapping (via
:class:`MetadataValueTransformer`), resolves per-row file paths and
keys, and assembles one :class:`SyncPlanRow` per record. The pushing
half lives in :mod:`uwazi_pusher`.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.adapters.execution.file_ops import pdf_filename_for
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.identity_config import KeySource
from browser_agent.domain.sync_plan import SyncAction, SyncPlan, SyncPlanRow
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from browser_agent.use_cases.metadata_db import parse_row_data, query_rows
from browser_agent.use_cases.metadata_value_transformer import (
    MetadataValueTransformer,
    build_thesaurus_parents,
    load_thesauri_mappings,
)
from browser_agent.use_cases.uwazi_mappers import to_template

from uwazi_api.client import UwaziClient
from uwazi_api.domain.entity import Entity
from uwazi_api.domain.search_filters import SearchFilters

_THESAURUS_TYPES = (FieldType.SELECT, FieldType.MULTI_SELECT, FieldType.RELATIONSHIP)
_ENTITY_BATCH = 200


def resolve_pdf_filename(record: dict, source_url: str, downloads_dir: Path | None) -> str | None:
    """Return the absolute local PDF path for one record, or ``None``."""
    raw = record.get("pdf_filename") or ""
    if raw and raw.strip() and not raw.startswith("no-pdf"):
        candidate = Path(raw)
        if not candidate.is_absolute() and downloads_dir is not None:
            candidate = downloads_dir / raw
        if candidate.exists():
            return str(candidate.resolve())
    pdf_url = record.get("pdf_url") or ""
    if pdf_url and downloads_dir is not None:
        candidate = downloads_dir / pdf_filename_for(pdf_url)
        if candidate.exists():
            return str(candidate.resolve())
    return None


def resolve_html_filename(record: dict, downloads_dir: Path | None) -> str | None:
    """Return the absolute local HTML path for one record, or ``None``."""
    raw = record.get("html_filename") or ""
    if raw and raw.strip():
        candidate = Path(raw)
        if not candidate.is_absolute() and downloads_dir is not None:
            candidate = downloads_dir / raw
        if candidate.exists():
            return str(candidate.resolve())
    return None


def resolve_key_value(record: dict, source_url: str, identity, mapping: UwaziMapping) -> str | None:
    """Return the per-row key value based on :class:`IdentityConfig`."""
    if identity.key_source is KeySource.FIELD:
        return _key_from_record(record, identity.key_field)
    if identity.key_source is KeySource.KEY_FIELD_AND_PROPERTY:
        value = _key_from_record(record, identity.key_field)
        return value if value is not None else source_url
    return _placeholder_value(source_url, identity.path_placeholder)


def _key_from_record(record: dict, key_field: str | None) -> str | None:
    """Return the ``key_field`` value from a record, or None when not set."""
    if not key_field:
        return None
    value = record.get(key_field)
    return None if value is None else str(value)


def _placeholder_value(source_url: str, placeholder: str | None) -> str | None:
    """Extract a placeholder value from a URL by pattern matching the URL pattern."""
    if not placeholder:
        return None
    idx = source_url.find(placeholder)
    return source_url[idx:] if idx >= 0 else None


def _title_of_record(record: dict, source_url: str, mapping: UwaziMapping) -> str:
    """Return the entity title for one record, falling back to the source URL."""
    title_prop = mapping.title_property()
    if title_prop is not None and title_prop.source:
        title = record.get(title_prop.source)
        if title:
            return str(title)
    return mapping.identity.path_placeholder or source_url


def _row_action(record, source_url, mapping, entities_by_key) -> tuple[SyncAction, str | None]:
    """Return the action + skip reason for one record."""
    if mapping.upload_pdf and not (record.get("pdf_filename") or "").strip():
        return SyncAction.SKIP, "no_local_pdf"
    if mapping.identity.key_source is not KeySource.KEY_FIELD_AND_PROPERTY:
        return SyncAction.CREATE, None
    key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
    if not key_value:
        return SyncAction.CREATE, None
    if entities_by_key.get(str(key_value).strip()):
        return SyncAction.SKIP, "already_on_uwazi"
    return SyncAction.CREATE, None


def _thesaurus_ids_from_mapping(template: UwaziTemplate, mapping: UwaziMapping) -> tuple[str, ...]:
    """Return the distinct non-null thesaurus ids from the live template for select/multiselect props."""
    seen: set[str] = set()
    for prop in mapping.properties:
        if prop.type in _THESAURUS_TYPES:
            tprop = template.property_by_name(prop.name)
            if tprop and tprop.thesaurus_id:
                seen.add(tprop.thesaurus_id)
    return tuple(seen)


def _fetch_existing_entities(client: UwaziClient, mapping: UwaziMapping) -> dict[str, str]:
    """Fetch and index existing Uwazi entities for ``mapping`` once per plan."""
    if mapping.identity.key_source is not KeySource.KEY_FIELD_AND_PROPERTY:
        return {}
    fetcher = ExistingEntitiesFetcher(client)
    return fetcher.fetch(
        template_name=mapping.template,
        language=mapping.default_language,
        key_property=mapping.identity.key_property or "",
        select_filter_name=mapping.identity.select_filtering_name,
        select_filter_values=mapping.identity.select_filtering_options,
    )


def _fetch_relationship_entity_mapping(
    client: UwaziClient, mapping: UwaziMapping, template: UwaziTemplate
) -> dict[str, dict[str, str]]:
    """Return ``{thesaurus_name: {entity_title: entity_shared_id}}`` for each relationship property."""
    result: dict[str, dict[str, str]] = {}
    for prop in mapping.properties:
        if prop.type is not FieldType.RELATIONSHIP or prop.thesaurus is None:
            continue
        tprop = template.property_by_name(prop.name)
        if tprop is None or tprop.thesaurus_id is None:
            continue
        target = client.templates.get_by_id(tprop.thesaurus_id)
        if target is None:
            continue
        entities = _fetch_all_entities_by_template(client, target.name, mapping.default_language)
        title_to_id = {e.title: e.shared_id for e in entities if e.title and e.shared_id}
        if title_to_id:
            result[prop.thesaurus] = title_to_id
    return result


def _fetch_all_entities_by_template(client: UwaziClient, template_name: str, language: str) -> list[Entity]:
    """Fetch every entity for ``template_name`` via paginated search."""
    out: list[Entity] = []
    start = 0
    while True:
        page = client.search.search_by_filter(
            filters=SearchFilters(filters={}),
            template_name=template_name,
            start_from=start,
            batch_size=_ENTITY_BATCH,
            language=language,
        )
        if not page:
            break
        out.extend(page)
        if len(page) < _ENTITY_BATCH:
            break
        start += _ENTITY_BATCH
    return out


def _build_plan_row(
    record,
    source_url,
    mapping,
    entities_by_key,
    transformer,
    thesaurus_lookup,
    thesaurus_parents,
    downloads_dir,
    relationship_title_to_id,
) -> SyncPlanRow:
    """Transform one record into one :class:`SyncPlanRow`."""
    pdf_path = resolve_pdf_filename(record, source_url, downloads_dir)
    record["pdf_filename"] = pdf_path
    html_path = resolve_html_filename(record, downloads_dir)
    action, skip_reason = _row_action(record, source_url, mapping, entities_by_key)
    return SyncPlanRow(
        action=action,
        language=mapping.default_language,
        source_url=source_url,
        title=_title_of_record(record, source_url, mapping),
        metadata=transformer.build_for_row(
            record, source_url, mapping, thesaurus_lookup, thesaurus_parents, relationship_title_to_id
        ),
        pdf_path=pdf_path,
        html_path=html_path,
        key_value=resolve_key_value(record, source_url, mapping.identity, mapping),
        mapping_sha256=mapping.sha256,
        skip_reason=skip_reason,
    )


def _plan_rows(records, mapping, client, thesaurus_lookup, transformer, downloads_dir) -> tuple[SyncPlanRow, ...]:
    """Transform every metadata row into one :class:`SyncPlanRow`."""
    template_raw = client.templates.get_by_name(mapping.template)
    if template_raw is None:
        raise ValueError(f"Uwazi template {mapping.template!r} not found")
    template = to_template(template_raw)
    entities_by_key = _fetch_existing_entities(client, mapping)
    thesaurus_ids = _thesaurus_ids_from_mapping(template, mapping)
    parents_by_id = build_thesaurus_parents(client, mapping.default_language, thesaurus_ids)
    thesaurus_parents: dict[str, dict[str, str | None]] = {}
    for prop in mapping.properties:
        if prop.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            tprop = template.property_by_name(prop.name)
            if tprop and tprop.thesaurus_id and tprop.thesaurus_id in parents_by_id:
                thesaurus_parents[prop.name] = parents_by_id[tprop.thesaurus_id]
    relationship_title_to_id = _fetch_relationship_entity_mapping(client, mapping, template)
    return tuple(
        _build_plan_row(
            parse_row_data(raw_data),
            source_url,
            mapping,
            entities_by_key,
            transformer,
            thesaurus_lookup,
            thesaurus_parents,
            downloads_dir,
            relationship_title_to_id,
        )
        for source_url, _task_slug, raw_data in records
    )


def execute(
    *,
    mapping: UwaziMapping,
    metadata_db_path: Path,
    client: UwaziClient,
    thesauri_mappings_dir: Path,
    run: str | None = None,
    downloads_dir: Path | None = None,
) -> SyncPlan:
    """Build a :class:`SyncPlan` from the live ``metadata.db`` rows."""
    thesaurus_lookup = load_thesauri_mappings(thesauri_mappings_dir)
    rows = query_rows(metadata_db_path, run)
    transformer = MetadataValueTransformer()
    return SyncPlan(mapping=mapping, rows=_plan_rows(rows, mapping, client, thesaurus_lookup, transformer, downloads_dir))
