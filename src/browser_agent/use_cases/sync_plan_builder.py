"""Build a :class:`SyncPlan` from a :class:`UwaziMapping` + ``metadata.db`` rows.

Pure data: no LLM, no side effects on Uwazi. Reads the metadata rows,
applies thesaurus substitution / date parsing / select wrapping (via
:class:`MetadataValueTransformer`), resolves per-row file paths and
keys, and assembles one :class:`SyncPlanRow` per record. The pushing
half lives in :mod:`uwazi_pusher`.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.adapters.execution.file_ops import file_ext_for, file_filename_for, pdf_filename_for
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.sync_plan import SyncAction, SyncPlan, SyncPlanRow
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from browser_agent.use_cases.metadata_db import normalize_record, parse_row_data, query_rows
from browser_agent.use_cases.metadata_value_transformer import (
    MetadataValueTransformer,
    build_thesaurus_parents,
    load_thesauri_mappings_by_property,
)
from browser_agent.use_cases.source_value_resolver import resolve_source_value
from browser_agent.use_cases.uwazi_mappers import to_template

from uwazi_api.client import UwaziClient
from uwazi_api.domain.entity import Entity
from uwazi_api.domain.search_filters import SearchFilters

_THESAURUS_TYPES = (FieldType.SELECT, FieldType.MULTI_SELECT, FieldType.RELATIONSHIP)
_ENTITY_BATCH = 200


def resolve_pdf_filename(record: dict, source_url: str, downloads_dir: Path | None) -> str | None:
    """Return the absolute local PDF path for one record, or ``None``.

    Non-PDF documents (``.doc``/``.docx``/``.rtf``/…) return ``None`` —
    :func:`resolve_supporting_filename` owns them.
    """
    raw = record.get("pdf_filename") or ""
    if file_ext_for(raw) != ".pdf" and file_ext_for(raw) != "":
        return None  # non-PDF document -> supporting path
    if raw and raw.strip() and not raw.startswith("no-pdf"):
        candidate = Path(raw)
        if not candidate.is_absolute() and downloads_dir is not None:
            candidate = downloads_dir / raw
        if candidate.exists():
            return str(candidate.resolve())
    file_url = record.get("file_url") or ""
    if file_url and downloads_dir is not None:
        candidate = downloads_dir / pdf_filename_for(file_url)
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


def resolve_supporting_filename(record: dict, raw_filename: str, downloads_dir: Path | None) -> str | None:
    """Return the absolute local supporting-file path for one record, or ``None``.

    ``raw_filename`` is the original ``pdf_filename`` basename captured
    before ``_build_plan_row`` mutates the record. Only non-PDF document
    basenames are resolved here; PDF (or unknown/extensionless) basenames
    belong to :func:`resolve_pdf_filename`.
    """
    raw = raw_filename or record.get("pdf_filename") or ""
    if not raw or raw.startswith("no-pdf"):
        return None
    if file_ext_for(raw) in (".pdf", ""):
        return None  # PDF (or unknown) -> resolve_pdf_filename owns it
    candidate = Path(raw)
    if not candidate.is_absolute() and downloads_dir is not None:
        candidate = downloads_dir / raw
    if candidate.exists():
        return str(candidate.resolve())
    file_url = record.get("file_url") or ""
    if file_url and downloads_dir is not None:
        cand2 = downloads_dir / file_filename_for(file_url)
        if cand2.exists():
            return str(cand2.resolve())
    return None


def resolve_key_value(record: dict, source_url: str, identity, mapping: UwaziMapping) -> str | None:
    """Return the per-row key value from ``identity.key_field``.

    The identity check is always: read ``key_field`` from the record and
    look for an existing Uwazi entity whose ``key_property`` matches.
    When ``key_field`` is unset or absent, the source URL is used as the
    fallback key so a row can still be matched against Uwazi.
    """
    value = _key_from_record(record, identity.key_field)
    return value if value is not None else source_url


def _key_from_record(record: dict, key_field: str | None) -> str | None:
    """Return the ``key_field`` value from a record, or None when not set."""
    if not key_field:
        return None
    value = record.get(key_field)
    return None if value is None else str(value)


def _title_of_record(record: dict, source_url: str, mapping: UwaziMapping) -> str:
    """Return the entity title for one record, falling back to the source URL."""
    title_prop = mapping.title_property()
    if title_prop is not None and title_prop.source:
        title = resolve_source_value(record, title_prop.source)
        if title:
            return str(title)
    return source_url


def _row_action(
    record,
    source_url,
    mapping,
    entities_by_key,
    registry_entities_by_key=None,
    primary_entities_by_key=None,
) -> tuple[SyncAction, str | None]:
    """Return the action + skip reason for one record.

    The identity check looks up ``key_field`` in the record and probes
    the existing-entity index built from Uwazi. When
    ``registry_entities_by_key`` is supplied (registry flow), the
    identity value prevails on the registry template: if it exists there
    the row is SKIP (already_on_registry); if it exists only in the
    primary template the action is CREATE_REGISTRY_ONLY (recover the
    missing registry entity); otherwise CREATE (both).

    A record whose data has no ``file_url`` is SKIP (``no_file_url``)
    and is never uploaded.
    """
    if not (record.get("file_url") or ""):
        return SyncAction.SKIP, "no_file_url"
    if not mapping.identity.key_property:
        return SyncAction.CREATE, None
    key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
    if not key_value:
        return SyncAction.CREATE, None
    key = str(key_value).strip()
    if registry_entities_by_key is not None:
        if registry_entities_by_key.get(key):
            return SyncAction.SKIP, "already_on_registry"
        if primary_entities_by_key and primary_entities_by_key.get(key):
            return SyncAction.CREATE_REGISTRY_ONLY, None
        return SyncAction.CREATE, None
    if entities_by_key.get(key):
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
    """Fetch and index existing Uwazi entities for ``mapping`` once per plan.

    The index spans every instance language so a row is treated as
    already uploaded when its link key value exists in any language.
    """
    if not mapping.identity.key_property:
        return {}
    fetcher = ExistingEntitiesFetcher(client)
    return fetcher.fetch(
        template_name=mapping.template,
        key_property=mapping.identity.key_property or "",
        select_filter_name=mapping.identity.select_filtering_name,
        select_filter_values=mapping.identity.select_filtering_options,
    )


def _fetch_existing_entities_for_template(
    client: UwaziClient,
    mapping: UwaziMapping,
    template_name: str,
) -> dict[str, str]:
    """Fetch and index existing entities for one template by the mapping's key property."""
    if not mapping.identity.key_property:
        return {}
    fetcher = ExistingEntitiesFetcher(client)
    return fetcher.fetch(
        template_name=template_name,
        key_property=mapping.identity.key_property or "",
        select_filter_name=mapping.identity.select_filtering_name,
        select_filter_values=mapping.identity.select_filtering_options,
    )


def _fetch_relationship_entity_mapping(
    client: UwaziClient, mapping: UwaziMapping, template: UwaziTemplate
) -> dict[str, dict[str, str]]:
    """Return ``{target_template_id: {entity_title: entity_shared_id}}`` for each relationship."""
    result: dict[str, dict[str, str]] = {}
    for prop in mapping.properties:
        if prop.type is not FieldType.RELATIONSHIP:
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
            result[tprop.thesaurus_id] = title_to_id
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
    thesaurus_parents,
    downloads_dir,
    relationship_title_to_id,
    thesaurus_lookup_by_property=None,
    registry_entities_by_key=None,
    primary_entities_by_key=None,
    registry_transformer=None,
    primary_shared_id_for_key=None,
) -> SyncPlanRow:
    """Transform one record into one :class:`SyncPlanRow`."""
    raw_filename = record.get("pdf_filename") or ""
    pdf_path = resolve_pdf_filename(record, source_url, downloads_dir)
    record["pdf_filename"] = pdf_path
    html_path = resolve_html_filename(record, downloads_dir)
    supporting_path = resolve_supporting_filename(record, raw_filename, downloads_dir)
    action, skip_reason = _row_action(
        record,
        source_url,
        mapping,
        entities_by_key,
        registry_entities_by_key=registry_entities_by_key,
        primary_entities_by_key=primary_entities_by_key,
    )
    key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
    registry_metadata: dict = {}
    primary_shared_id: str | None = None
    if mapping.registry_template and registry_transformer is not None and action is not SyncAction.SKIP:
        registry_metadata = registry_transformer.build_registry_metadata_for_row(
            record,
            source_url,
            mapping,
            thesaurus_lookup_by_property,
            thesaurus_parents,
            relationship_title_to_id,
        )
    if action is SyncAction.CREATE_REGISTRY_ONLY and primary_entities_by_key is not None:
        primary_shared_id = primary_entities_by_key.get(str(key_value).strip()) if key_value else None
    return SyncPlanRow(
        action=action,
        language=mapping.default_language,
        source_url=source_url,
        title=_title_of_record(record, source_url, mapping),
        metadata=transformer.build_for_row(
            record,
            source_url,
            mapping,
            thesaurus_lookup_by_property,
            thesaurus_parents,
            relationship_title_to_id,
        ),
        pdf_path=pdf_path,
        html_path=html_path,
        supporting_path=supporting_path,
        key_value=key_value,
        mapping_sha256=mapping.sha256,
        skip_reason=skip_reason,
        registry_metadata=registry_metadata,
        primary_shared_id=primary_shared_id,
    )


def _plan_rows(records, mapping, client, thesaurus_lookup_by_property, downloads_dir) -> tuple[SyncPlanRow, ...]:
    """Transform every metadata row into one :class:`SyncPlanRow`."""
    template_raw = client.templates.get_by_name(mapping.template)
    if template_raw is None:
        raise ValueError(f"Uwazi template {mapping.template!r} not found")
    template = to_template(template_raw)
    transformer = MetadataValueTransformer(template=template)
    entities_by_key = _fetch_existing_entities(client, mapping)
    registry_template: UwaziTemplate | None = None
    registry_entities_by_key: dict[str, str] | None = None
    primary_entities_by_key: dict[str, str] | None = None
    registry_transformer: MetadataValueTransformer | None = None
    if mapping.registry_template:
        registry_raw = client.templates.get_by_name(mapping.registry_template)
        if registry_raw is None:
            raise ValueError(f"Uwazi registry template {mapping.registry_template!r} not found")
        registry_template = to_template(registry_raw)
        registry_transformer = MetadataValueTransformer(template=registry_template)
        registry_entities_by_key = _fetch_existing_entities_for_template(client, mapping, mapping.registry_template)
        primary_entities_by_key = entities_by_key
    thesaurus_ids = _thesaurus_ids_from_mapping(template, mapping)
    if registry_template is not None:
        thesaurus_ids = thesaurus_ids + _thesaurus_ids_from_mapping(registry_template, mapping)
    parents_by_id = build_thesaurus_parents(client, mapping.default_language, thesaurus_ids)
    thesaurus_parents: dict[str, dict[str, str | None]] = {}
    for prop in mapping.properties:
        if prop.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            ref_template = registry_template if mapping.registry_template in prop.template_name else template
            tprop = ref_template.property_by_name(prop.name) if ref_template else template.property_by_name(prop.name)
            if tprop and tprop.thesaurus_id and tprop.thesaurus_id in parents_by_id:
                thesaurus_parents[prop.name] = parents_by_id[tprop.thesaurus_id]
    relationship_title_to_id = _fetch_relationship_entity_mapping(client, mapping, template)
    return tuple(
        _build_plan_row(
            normalize_record(parse_row_data(raw_data)),
            source_url,
            mapping,
            entities_by_key,
            transformer,
            thesaurus_parents,
            downloads_dir,
            relationship_title_to_id,
            thesaurus_lookup_by_property,
            registry_entities_by_key=registry_entities_by_key,
            primary_entities_by_key=primary_entities_by_key,
            registry_transformer=registry_transformer,
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
    thesaurus_lookup_by_property = load_thesauri_mappings_by_property(thesauri_mappings_dir)
    rows = query_rows(metadata_db_path, run)
    return SyncPlan(mapping=mapping, rows=_plan_rows(rows, mapping, client, thesaurus_lookup_by_property, downloads_dir))
