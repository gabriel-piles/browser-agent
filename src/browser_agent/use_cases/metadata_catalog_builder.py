"""Build a :class:`MetadataFieldCatalog` from ``metadata.db`` rows.

The propose driver delegates here so the value-type heuristics, row
aggregation and catalog assembly live behind one small object instead
of a stack of free functions in the script.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from browser_agent.domain.metadata_field import MetadataField
from browser_agent.domain.metadata_field_catalog import MetadataFieldCatalog
from browser_agent.domain.run_config import RunConfig
from browser_agent.use_cases.metadata_db import parse_row_data, query_rows
from browser_agent.use_cases.metadata_value_type_heuristic import MetadataValueTypeHeuristic


class MetadataCatalogBuilder:
    """Aggregate ``metadata.db`` rows into a :class:`MetadataFieldCatalog`."""

    def __init__(
        self,
        run_config: RunConfig,
        value_type: MetadataValueTypeHeuristic | None = None,
    ) -> None:
        self._run = run_config.name
        self._max_fields = run_config.max_fields_in_prompt
        self._examples_per_field = run_config.examples_per_field
        self._value_type = value_type or MetadataValueTypeHeuristic()

    def build(self, db_path: Path) -> tuple[MetadataFieldCatalog | None, int]:
        """Return ``(catalog, total_rows)`` from the rows in ``db_path``.

        ``catalog`` is ``None`` when the database has no rows at all.
        """
        rows = self._query_rows(db_path)
        if not rows:
            return None, 0
        distinct, page_count, total_rows = self._aggregate(rows)
        return self._assemble(distinct, page_count, total_rows), total_rows

    def _query_rows(self, db_path: Path) -> list[tuple[str, str, str]]:
        """Return ``(source_url, task_slug, data_json)`` rows from ``metadata.db``."""
        return query_rows(db_path)

    def _aggregate(self, rows) -> tuple[dict, Counter, int]:
        """Walk every row, building per-field distinct samples + a page count."""
        distinct: dict[str, list[str]] = {}
        page_count: Counter[str] = Counter()
        total_rows = 0
        for _source_url, _task_slug, raw_data in rows:
            record = self._parse_row(raw_data)
            if not record:
                continue
            total_rows += 1
            for name, value in record.items():
                self._record(name, value, distinct, page_count)
        return distinct, page_count, total_rows

    def _parse_row(self, raw: str | None) -> dict:
        """Decode a single ``metadata.data`` JSON blob (``{}`` on failure)."""
        return parse_row_data(raw)

    def _record(self, name, value, distinct: dict, page_count: Counter) -> None:
        """Record per-field stats; lists are expanded one element at a time.

        ``page_count`` stays row-level (one row = one page observed
        with the field) so the field's per-page statistic is
        unaffected by how many values a row carries. ``None`` and
        empty-string elements inside the list are skipped. Scalars
        and other JSON shapes fall through to the single-record path.
        """
        if not isinstance(name, str) or not name:
            return
        if value in (None, ""):
            return
        if isinstance(value, (list, tuple)):
            page_count[name] += 1
            for item in value:
                self._record_sample(name, item, distinct)
            return
        page_count[name] += 1
        self._record_sample(name, value, distinct)

    def _record_sample(self, name, value, distinct: dict) -> None:
        """Record one scalar sample into the per-field distinct bucket."""
        if value in (None, ""):
            return
        text = str(value).strip()
        if not text:
            return
        bucket = distinct.setdefault(name, [])
        if text not in bucket and len(bucket) < self._examples_per_field:
            bucket.append(text)

    def _assemble(self, distinct, page_count, total_rows) -> MetadataFieldCatalog:
        """Assemble the final catalog from the per-field stats."""
        kept = [
            self._make_field(name, distinct.get(name, ()), page_count, total_rows)
            for name, _ in page_count.most_common(self._max_fields)
        ]
        return MetadataFieldCatalog(
            run=self._run,
            pattern="",
            sample_urls=(),
            fields=kept,
            cohesion_assessment=self._cohesion(total_rows),
        )

    def _make_field(self, name, samples, page_count, total_rows) -> MetadataField:
        """Build a single :class:`MetadataField` from accumulated stats."""
        return MetadataField(
            name=name,
            description=(f"Field observed on {page_count[name]}/{total_rows} row(s) in metadata.db."),
            value_type=self._value_type.infer(list(samples)),
            examples=tuple(samples),
            export_to_uwazi=True,
        )

    def _cohesion(self, total_rows: int) -> str:
        """Return the human-readable note describing how the catalog was derived."""
        return f"Catalog derived from {total_rows} row(s) in metadata.db for run {self._run!r}."
