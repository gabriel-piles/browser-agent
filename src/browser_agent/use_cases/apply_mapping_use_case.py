"""The use case that builds a :class:`SyncPlan` from a :class:`UwaziMapping`.

Pure data: no LLM. The use case transforms every row of the
``metadata.db`` table into one :class:`SyncPlanRow`, applying
thesaurus value substitution, date parsing, and per-language
normalisation. It then asks the :class:`uwazi_api.client.UwaziClient`
to create entities for new rows and skip rows that already exist.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

from browser_agent.domain.apply_result import ApplyResult
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.identity_config import KeySource
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.sync_plan import SyncAction, SyncPlan, SyncPlanRow
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from uwazi_api.client import UwaziClient
from uwazi_api.domain.entity import Entity

# The set of field types whose value should be substituted via the
# per-thesaurus mapping YAML. Kept module-level so the substitution
# helpers and the field-type dispatch are easy to read.
_THESAURUS_TYPES = (FieldType.SELECT, FieldType.MULTI_SELECT)

# Default date formats tried in order when a :class:`FieldMapping` does
# not pin its own ``parse_formats``. Operator overrides always win.
_DEFAULT_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y")


def _parse_row_data(raw: str | None) -> dict:
    """Decode the ``metadata.data`` JSON blob of one row, returning ``{}`` on failure."""
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def resolve_pdf_filename(record: dict, source_url: str, downloads_dir: Path | None) -> str | None:
    """Return the local PDF path for one record, or ``None``.

    The scraper stores ``pdf_url`` per row; the downloaded files land in
    ``<run>/downloads/`` with the URL basename as the filename. This
    helper bridges the two: it derives the basename from the record's
    ``pdf_url`` (falling back to ``source_url``), checks the file
    exists on disk, and returns its absolute path string when it does.

    Rows whose ``pdf_url`` is empty or whose file was not downloaded
    (e.g. the ``no-pdf-*`` placeholder rows) return ``None`` so the
    upload-validation report and the apply plan stay consistent.
    """
    if downloads_dir is None:
        return None
    url = record.get("pdf_url") or source_url
    if not isinstance(url, str) or not url:
        return None
    tail = url.rstrip("/").split("/")[-1]
    if not tail or "." not in tail or tail.startswith("?"):
        return None
    candidate = downloads_dir / tail
    if candidate.is_file():
        return str(candidate)
    return None


def _query_metadata_rows(db_path: Path, run: str | None) -> list[tuple[str, str, str]]:
    """Return ``(source_url, task_slug, data_json)`` rows from ``metadata.db``."""
    conn = sqlite3.connect(str(db_path))
    try:
        if run is not None:
            return conn.execute(
                "SELECT source_url, task_slug, data FROM metadata WHERE task_slug = ?",
                (run,),
            ).fetchall()
        return conn.execute("SELECT source_url, task_slug, data FROM metadata").fetchall()
    finally:
        conn.close()


def _try_parse_date(text: str, fmt: str) -> str | None:
    """Return the ISO date string for ``text`` parsed with ``fmt``, or None."""
    try:
        return datetime.strptime(text, fmt).date().isoformat()
    except ValueError:
        return None


def _coerce_date(value, parse_formats: tuple[str, ...]) -> str | None:
    """Parse a date string with the first matching format, or pass it through."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in parse_formats or _DEFAULT_DATE_FORMATS:
        parsed = _try_parse_date(text, fmt)
        if parsed is not None:
            return parsed
    return text


def _substitute_one(value, lookup: dict[str, str]) -> object:
    """Apply a single thesaurus lookup to one value, preserving ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return lookup.get(text, value)


def _substitute_thesaurus(
    value,
    field_type: FieldType,
    lookup: dict[str, str] | None,
) -> object:
    """Substitute a crawl value with its canonical thesaurus form (or pass through)."""
    if field_type not in _THESAURUS_TYPES or not lookup:
        return value
    if isinstance(value, list):
        return [_substitute_one(item, lookup) for item in value]
    return _substitute_one(value, lookup)


def _thesauri_dict_from_yaml(path: Path) -> dict[str, str]:
    """Load a single ``thesauri_mappings/*.yaml`` into a crawl -> uwazi dict."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("entries") or ()
    return {e["crawl_value"]: e["uwazi_value"] for e in entries if isinstance(e, dict) and e.get("uwazi_value") is not None}


def load_thesauri_mappings(thesauri_dir: Path) -> dict[str, dict[str, str]]:
    """Read every ``thesauri_mappings/*.yaml`` into a name -> {crawl: uwazi} dict.

    Public so :mod:`browser_agent.drivers.step_2_uwazi_match` can reuse
    the on-disk cache when it builds per-row metadata for the
    upload-validation report (no LLM is involved there).
    """
    out: dict[str, dict[str, str]] = {}
    if not thesauri_dir.is_dir():
        return out
    for path in sorted(thesauri_dir.glob("*.yaml")):
        data = _thesauri_dict_from_yaml(path)
        if not data:
            continue
        out[path.stem] = data
    return out


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
    needle = placeholder
    idx = source_url.find(needle)
    return source_url[idx:] if idx >= 0 else None


def resolve_key_value(record: dict, source_url: str, identity, mapping: UwaziMapping) -> str | None:
    """Return the per-row key value based on :class:`IdentityConfig`."""
    if identity.key_source is KeySource.FIELD:
        return _key_from_record(record, identity.key_field)
    if identity.key_source is KeySource.KEY_FIELD_AND_PROPERTY:
        value = _key_from_record(record, identity.key_field)
        return value if value is not None else source_url
    return _placeholder_value(source_url, identity.path_placeholder)


def _title_of_record(record: dict, mapping: UwaziMapping) -> str:
    """Return the entity title for one record, falling back to the source URL."""
    title_prop = mapping.title_property()
    if title_prop is not None:
        title = record.get(title_prop.source)
        if title:
            return str(title)
    return mapping.identity.path_placeholder or source_url


def _format_metadata_field(raw, prop: MappedProperty, lookup) -> object:
    """Apply type-specific transformation to one property's raw value."""
    if prop.type is FieldType.DATE:
        return _coerce_date(raw, prop.parse_formats)
    if prop.type in _THESAURUS_TYPES:
        return _substitute_thesaurus(raw, prop.type, lookup)
    return raw


def _default_metadata_value(prop: MappedProperty, lookup) -> object:
    """Coerce a constant ``default_value`` for a source=None entry."""
    if prop.default_value is None:
        return None
    return _format_metadata_field(prop.default_value, prop, lookup)


def build_metadata_for_row(
    record: dict,
    source_url: str,
    mapping: UwaziMapping,
    thesaurus_lookup: dict[str, dict[str, str]],
) -> dict:
    """Build the post-transform metadata dict for one record.

    Public so :mod:`browser_agent.drivers.step_2_uwazi_match` can build
    the per-row metadata for the upload-validation report without
    duplicating the thesaurus-substitution logic.
    """
    out: dict = {}
    for prop in mapping.properties:
        if prop.type in (FieldType.TITLE, FieldType.SKIPPED, FieldType.FILE):
            continue
        lookup = thesaurus_lookup.get(prop.thesaurus) if prop.thesaurus else None
        if prop.source is None:
            value = _default_metadata_value(prop, lookup)
            if value is not None:
                out[prop.name] = value
            continue
        if prop.source not in record:
            continue
        out[prop.name] = _format_metadata_field(record[prop.source], prop, lookup)
    if mapping.identity.source_url_property:
        out[mapping.identity.source_url_property] = source_url
    return out


def _fetch_existing_entities(client: UwaziClient, mapping: UwaziMapping) -> dict[str, str]:
    """Fetch and index existing Uwazi entities for ``mapping`` once per plan."""
    if mapping.identity.key_source is not KeySource.KEY_FIELD_AND_PROPERTY:
        return {}
    fetcher = ExistingEntitiesFetcher(client)
    return fetcher.fetch(
        template_name=mapping.template,
        language=mapping.default_language,
        key_property=mapping.identity.key_property or "",
    )


def _row_action(record, source_url, mapping, entities_by_key) -> SyncAction:
    """Return CREATE for new rows, SKIP for rows that already exist on Uwazi."""
    if mapping.identity.key_source is not KeySource.KEY_FIELD_AND_PROPERTY:
        return SyncAction.CREATE
    key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
    if not key_value:
        return SyncAction.CREATE
    if entities_by_key.get(str(key_value).strip()):
        return SyncAction.SKIP
    return SyncAction.CREATE


def _build_plan_row(
    record, source_url, mapping, entities_by_key, thesaurus_lookup, downloads_dir: Path | None = None
) -> SyncPlanRow:
    """Transform one record into one :class:`SyncPlanRow`."""
    record.setdefault("pdf_filename", resolve_pdf_filename(record, source_url, downloads_dir))
    language = mapping.default_language
    action = _row_action(record, source_url, mapping, entities_by_key)
    return SyncPlanRow(
        action=action,
        language=language,
        source_url=source_url,
        title=_title_of_record(record, mapping),
        metadata=build_metadata_for_row(record, source_url, mapping, thesaurus_lookup),
        pdf_path=record.get("pdf_filename") or None,
        key_value=resolve_key_value(record, source_url, mapping.identity, mapping),
        mapping_sha256=mapping.sha256,
    )


def _plan_rows(records, mapping, client, thesaurus_lookup, downloads_dir: Path | None = None) -> tuple[SyncPlanRow, ...]:
    """Transform every metadata row into one :class:`SyncPlanRow`."""
    entities_by_key = _fetch_existing_entities(client, mapping)
    return tuple(
        _build_plan_row(_parse_row_data(raw_data), source_url, mapping, entities_by_key, thesaurus_lookup, downloads_dir)
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
    """Build a :class:`SyncPlan` from the live ``metadata.db`` rows.

    ``run`` filters ``metadata`` rows by ``task_slug`` when not None;
    pass None to push every row in the table. ``downloads_dir`` is the
    active run's ``downloads/`` directory used to resolve each row's
    local ``pdf_filename`` from its ``pdf_url`` basename.
    """
    thesaurus_lookup = load_thesauri_mappings(thesauri_mappings_dir)
    rows = _query_metadata_rows(metadata_db_path, run)
    return SyncPlan(mapping=mapping, rows=_plan_rows(rows, mapping, client, thesaurus_lookup, downloads_dir))


def empty_apply_result() -> ApplyResult:
    """Return a fresh :class:`ApplyResult` for the no-LLM apply driver."""
    return ApplyResult(per_language_counts={}, skip_reasons=(), error_rows=())


def _record_result(out: ApplyResult, language: str, action: SyncAction) -> None:
    """Increment the per-language count for ``action``."""
    bucket = out.per_language_counts.setdefault(language, {})
    bucket[action.value] = bucket.get(action.value, 0) + 1


def _record_skip(out: ApplyResult, language: str, source_url: str, reason: str) -> None:
    """Append one skip row to the apply result and bump the per-language count."""
    out.skip_reasons = out.skip_reasons + ((language, source_url, reason),)
    _record_result(out, language, SyncAction.SKIP)


def _record_error(out: ApplyResult, language: str, source_url: str, message: str) -> None:
    """Append one error row to the apply result."""
    out.error_rows = out.error_rows + ((language, source_url, message),)


def _upload_primary_file(client: UwaziClient, shared_id: str, pdf_path: str, language: str, title: str) -> None:
    """Attach ``pdf_path`` as the primary document of the entity."""
    client.files.upload_file(pdf_file_path=str(pdf_path), share_id=shared_id, language=language, title=title)


def _create_entity_for_row(client: UwaziClient, row, mapping) -> str:
    """Create a fresh Uwazi entity for one CREATE row, return the new shared id."""
    entity = Entity(template=mapping.template, title=row.title)
    shared_id = client.entities.upload(entity=entity, language=row.language)
    entity = Entity(sharedId=shared_id, metadata=row.metadata)
    client.entities.update_partially(entity=entity, language=row.language)
    if mapping.upload_pdf and row.pdf_path:
        _upload_primary_file(client, shared_id, row.pdf_path, row.language, row.title)
    return shared_id


def _push_row(client: UwaziClient, out, row, mapping) -> None:
    """Push one :class:`SyncPlanRow` to Uwazi and update ``out`` accordingly."""
    try:
        if row.action is SyncAction.CREATE:
            _create_entity_for_row(client, row, mapping)
        elif row.action is SyncAction.SKIP:
            _record_skip(out, row.language, row.source_url, "skipped_by_plan")
            return
        _record_result(out, row.language, row.action)
    except Exception as exc:  # noqa: BLE001 - any failure is recorded
        _record_error(out, row.language, row.source_url, str(exc))


def push_plan(
    *,
    plan: SyncPlan,
    client: UwaziClient,
) -> ApplyResult:
    """Push the plan to Uwazi; no LLM, pure :class:`UwaziClient` calls."""
    out = empty_apply_result()
    for row in plan.rows:
        _push_row(client, out, row, plan.mapping)
    return out
