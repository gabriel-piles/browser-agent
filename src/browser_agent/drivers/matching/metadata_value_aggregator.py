"""Aggregate every distinct value per source field from ``metadata.db``.

Hides the sqlite read + per-row value filtering + per-field
:func:`collections.Counter` aggregation behind one object so
the match driver does not reimplement it. The aggregator
returns a ``field_name -> Counter(value -> count)`` map ready
to feed the per-thesaurus matching pipeline.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


class MetadataValueAggregator:
    """Aggregate ``metadata.db`` rows into per-field value :class:`Counter` objects."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def aggregate(self) -> dict[str, Counter]:
        """Return ``field_name -> Counter(value -> count)`` for every row."""
        field_counters: dict[str, Counter] = {}
        for _source_url, _task_slug, raw_data in self._query_rows():
            fields = self._parse_row(raw_data)
            if not isinstance(fields, dict):
                continue
            for name, value in fields.items():
                self._record(name, value, field_counters)
        return field_counters

    def _query_rows(self) -> list[tuple[str, str, str]]:
        """Return ``(source_url, task_slug, data_json)`` rows from ``metadata.db``."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            return conn.execute("SELECT source_url, task_slug, data FROM metadata").fetchall()
        finally:
            conn.close()

    def _parse_row(self, raw: str | None) -> dict:
        """Decode a single row's JSON blob, returning ``{}`` on failure."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _record(self, name, value, field_counters: dict[str, Counter]) -> None:
        """Record one (name, value) pair, skipping empty names and values."""
        if not isinstance(name, str) or not name:
            return
        if value in (None, ""):
            return
        text = str(value).strip()
        if not text:
            return
        field_counters.setdefault(name, Counter())[text] += 1
