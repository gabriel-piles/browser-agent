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
from typing import Iterable
from urllib.parse import urlparse

from browser_agent.domain.apply_result import ApplyResult
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.identity_config import KeySource
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.sync_plan import SyncAction, SyncPlan, SyncPlanRow
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from uwazi_api.client import UwaziClient
from uwazi_api.domain.entity import Entity
from uwazi_api.domain.thesauri_value import ThesauriValue

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
    """Return the absolute local PDF path for one record, or ``None``.

    The scraper stores the on-disk filename in the record's
    ``pdf_filename`` field (e.g. ``pdf_001_01.pdf``); the file lands in
    ``<run>/downloads/``. The record's ``pdf_url`` is an opaque
    download token whose tail is NOT the saved filename, so it is only
    used as a fallback when ``pdf_filename`` is missing. Returns the
    absolute path string when the file exists on disk, else ``None``.
    """
    if downloads_dir is None:
        return None
    name = record.get("pdf_filename")
    if not isinstance(name, str) or not name:
        url = record.get("pdf_url") or source_url
        if not isinstance(url, str) or not url:
            return None
        name = url.rstrip("/").split("/")[-1]
        if not name or "." not in name or name.startswith("?"):
            return None
    candidate = downloads_dir / name
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


def _thesaurus_parents_from_tree(
    values: Iterable[ThesauriValue],
    out: dict[str, str | None],
    parent_label: str | None = None,
) -> None:
    """Walk a thesaurus tree and fill ``out`` with each label's parent label (or None)."""
    for v in values:
        out[v.label] = parent_label
        if v.values:
            _thesaurus_parents_from_tree(v.values, out, parent_label=v.label)


def build_thesaurus_parents(
    client: UwaziClient,
    language: str,
    thesaurus_ids: Iterable[str],
) -> dict[str, dict[str, str | None]]:
    """Return ``{thesaurus_id: {label: parent_label_or_None}}`` for ``thesaurus_ids``.

    Reads the live thesaurus once per language (the client caches the
    full list, so the cost is one HTTP round trip total) and walks
    each requested id's tree. Labels with no parent (top-level groups
    and the rare flat thesaurus) map to ``None``; child labels map to
    their direct parent's label.

    Used by the metadata builder to add the ``parent`` block the Uwazi
    validator expects for child select/multiselect values: a top-level
    label can be sent bare, but a child label has to carry the
    ``parent.label`` so the server can locate the right group.
    """
    wanted = set(thesaurus_ids)
    if not wanted:
        return {}
    out: dict[str, dict[str, str | None]] = {}
    for thesaurus in client.thesauris.get(language):
        if thesaurus.id not in wanted:
            continue
        parents: dict[str, str | None] = {}
        _thesaurus_parents_from_tree(thesaurus.values, parents)
        out[thesaurus.id] = parents
    return out


def _wrap_select_item(
    label: str,
    parents_map: dict[str, str | None] | None,
) -> dict:
    """Wrap one select/multiselect label as the Uwazi ``{value, parent?}`` item.

    Top-level labels (or anything outside ``parents_map``) carry no
    ``parent`` block — the server resolves them from the label. Child
    labels get the parent's label so the server can locate the right
    group in the thesaurus tree.
    """
    item: dict = {"value": label}
    if parents_map and label in parents_map:
        parent_label = parents_map[label]
        if parent_label is not None:
            item["parent"] = {"label": parent_label}
    return item


def _wrap_select_value(
    value,
    parents_map: dict[str, str | None] | None,
) -> list[dict] | None:
    """Wrap a select/multiselect value (scalar or list) as a list of items.

    Returns ``None`` for ``None``/empty values so the caller can skip
    the property entirely. Strings produce a one-item list; lists
    produce one item per element. Each item is a
    ``{value, parent?}`` dict the Uwazi ``Entity.metadata`` schema
    accepts verbatim.
    """
    if value is None or value == "":
        return None
    if isinstance(value, list):
        items = [_wrap_select_item(str(v), parents_map) for v in value if v is not None and v != ""]
        return items or None
    return [_wrap_select_item(str(value), parents_map)]


def _link_value(label: str, url: str) -> dict:
    """Build the ``{label, url}`` dict the Uwazi link property expects."""
    return {"label": label, "url": url}


def _looks_like_url(value: str) -> bool:
    """Return True when ``value`` is a parseable absolute URL with a non-empty host.

    The Uwazi server rejects link values whose ``url`` is not a valid
    URL (zod's ``invalid_string`` check on the link property). Empty
    strings and placeholders like ``no-pdf-12`` are not URLs, so the
    metadata builder must skip the link property for them rather than
    round-trip the placeholder through to Uwazi.
    """
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


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


def _title_of_record(record: dict, source_url: str, mapping: UwaziMapping) -> str:
    """Return the entity title for one record, falling back to the source URL.

    The title comes from the :class:`MappedProperty` whose ``type`` is
    :attr:`FieldType.TITLE` (the template's ``title`` common property,
    set by :class:`ProposeMappingUseCase`); Uwazi stores it on the
    entity itself (``Entity.title``), not in ``metadata``.
    """
    title_prop = mapping.title_property()
    if title_prop is not None and title_prop.source:
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
    thesaurus_parents: dict[str, dict[str, str | None]] | None = None,
) -> dict:
    """Build the post-transform metadata dict for one record.

    The title (and any other non-metadata-bound property such as the
    primary file) is skipped here because Uwazi stores it on the
    entity itself, not in ``Entity.metadata``. The apply step sends
    the title via ``Entity.title`` (see :func:`_create_entity_for_row`).

    Public so :mod:`browser_agent.drivers.step_2_uwazi_match` can build
    the per-row metadata for the upload-validation report without
    duplicating the thesaurus-substitution logic.

    ``thesaurus_parents`` is the ``{thesaurus_id: {label: parent_label}}``
    map built by :func:`build_thesaurus_parents`. It is only consulted
    for select/multiselect properties whose ``thesaurus`` id has an
    entry; absent or empty input means the rows are sent as bare
    labels (which the Uwazi validator accepts for top-level labels
    but rejects for child labels).
    """
    out: dict = {}
    for prop in mapping.properties:
        if prop.type in (FieldType.TITLE, FieldType.SKIPPED, FieldType.FILE):
            continue
        lookup = thesaurus_lookup.get(prop.thesaurus) if prop.thesaurus else None
        if prop.source is None:
            value = _default_metadata_value(prop, lookup)
            if value is not None:
                out[prop.name] = _coerce_metadata_value(value, prop, thesaurus_parents)
            continue
        if prop.source not in record:
            continue
        value = _format_metadata_field(record[prop.source], prop, lookup)
        coerced = _coerce_metadata_value(value, prop, thesaurus_parents)
        if coerced is not None:
            out[prop.name] = coerced
    if mapping.identity.source_url_property and _looks_like_url(source_url):
        out[mapping.identity.source_url_property] = _link_value(source_url, source_url)
    return out


def _coerce_metadata_value(
    value,
    prop: MappedProperty,
    thesaurus_parents: dict[str, dict[str, str | None]] | None,
) -> object:
    """Coerce a transformed value into the shape Uwazi's metadata expects.

    Select/multiselect values are wrapped as ``[{value, parent?}]`` so
    the Uwazi validator can locate child labels in the right group.
    Link values become ``{label, url}`` dicts. Plain string-typed
    properties are stringified because Uwazi's validator rejects
    non-string scalars (the scraper sometimes hands us integers from
    JSON-decoded metadata blobs). Other property types pass through
    unchanged.
    """
    if value is None or value == "":
        return None
    if prop.type in _THESAURUS_TYPES:
        parents_map = thesaurus_parents.get(prop.thesaurus_id) if (thesaurus_parents and prop.thesaurus_id) else None
        return _wrap_select_value(value, parents_map)
    if prop.type is FieldType.LINK:
        text = str(value)
        return _link_value(text, text)
    if prop.type is FieldType.TEXT and not isinstance(value, str):
        return str(value)
    return value


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


def _row_action(record, source_url, mapping, entities_by_key) -> tuple[SyncAction, str | None]:
    """Return the action + skip reason for one record.

    A row becomes ``SKIP`` when the mapping says ``upload_pdf`` is on
    but the record has no local PDF to upload (the no-pdf placeholder
    rows, or any row whose ``pdf_url`` was never downloaded). The
    caller records the reason so the operator can see the no-pdf
    rows in the apply report without them being treated as errors.
    Other rows are ``CREATE`` unless the existing-entity index
    already maps their key value to a Uwazi ``shared_id``.
    """
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


def _build_plan_row(
    record, source_url, mapping, entities_by_key, thesaurus_lookup, thesaurus_parents, downloads_dir: Path | None = None
) -> SyncPlanRow:
    """Transform one record into one :class:`SyncPlanRow`."""
    pdf_path = resolve_pdf_filename(record, source_url, downloads_dir)
    record["pdf_filename"] = pdf_path
    language = mapping.default_language
    action, skip_reason = _row_action(record, source_url, mapping, entities_by_key)
    return SyncPlanRow(
        action=action,
        language=language,
        source_url=source_url,
        title=_title_of_record(record, source_url, mapping),
        metadata=build_metadata_for_row(record, source_url, mapping, thesaurus_lookup, thesaurus_parents),
        pdf_path=pdf_path,
        key_value=resolve_key_value(record, source_url, mapping.identity, mapping),
        mapping_sha256=mapping.sha256,
        skip_reason=skip_reason,
    )


def _thesaurus_ids_from_mapping(mapping: UwaziMapping) -> tuple[str, ...]:
    """Return the distinct non-null thesaurus ids declared by select/multiselect props."""
    seen: set[str] = set()
    for prop in mapping.properties:
        if prop.type in _THESAURUS_TYPES and prop.thesaurus_id:
            seen.add(prop.thesaurus_id)
    return tuple(seen)


def _plan_rows(records, mapping, client, thesaurus_lookup, downloads_dir: Path | None = None) -> tuple[SyncPlanRow, ...]:
    """Transform every metadata row into one :class:`SyncPlanRow`."""
    entities_by_key = _fetch_existing_entities(client, mapping)
    thesaurus_parents = build_thesaurus_parents(
        client,
        mapping.default_language,
        _thesaurus_ids_from_mapping(mapping),
    )
    return tuple(
        _build_plan_row(
            _parse_row_data(raw_data),
            source_url,
            mapping,
            entities_by_key,
            thesaurus_lookup,
            thesaurus_parents,
            downloads_dir,
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
    """Attach ``pdf_path`` as the primary document of the entity.

    Uses ``/api/files/upload/document`` (not the attachment endpoint)
    so the file lands in ``Entity.documents`` — without that call the
    entity is created but its primary-document slot stays empty and
    the document does not appear in the Uwazi library. Returns silently
    on upload failure (the entity is still created; the operator sees
    the missing file in the library).
    """
    from uwazi_api.domain.FileType import FileType

    payload = Path(pdf_path).read_bytes()
    client.files.upload_document_from_bytes(
        file_bytes=payload,
        share_id=shared_id,
        language=language,
        title=title,
        file_type=FileType.PDF,
    )


def _create_entity_for_row(client: UwaziClient, row, mapping) -> str:
    """Create a fresh Uwazi entity for one CREATE row, return the new shared id.

    The title is sent as the top-level ``Entity.title`` field — Uwazi
    does NOT carry the title inside ``Entity.metadata``. The metadata
    blob built by :func:`build_metadata_for_row` already excludes the
    title (and the primary file) for the same reason.

    The metadata must be included in the first ``upload`` call: the
    Uwazi validator checks every required template property on the
    payload it receives, so an entity with no ``metadata`` would fail
    the check even when all required properties have defaults.
    """
    entity = Entity(
        template=mapping.template,
        title=row.title,
        published=mapping.publish,
        metadata=row.metadata,
    )
    shared_id = client.entities.upload(entity=entity, language=row.language)
    if mapping.upload_pdf and row.pdf_path:
        _upload_primary_file(client, shared_id, row.pdf_path, row.language, row.title)
    return shared_id


def _push_row(client: UwaziClient, out, row, mapping) -> None:
    """Push one :class:`SyncPlanRow` to Uwazi and update ``out`` accordingly."""
    try:
        if row.action is SyncAction.CREATE:
            _create_entity_for_row(client, row, mapping)
        elif row.action is SyncAction.SKIP:
            _record_skip(out, row.language, row.source_url, row.skip_reason or "skipped_by_plan")
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
