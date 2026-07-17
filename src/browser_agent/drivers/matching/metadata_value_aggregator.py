"""Aggregate every distinct value per source field from ``metadata.db``.

Hides the sqlite read + per-row value filtering + per-field
:func:`collections.Counter` aggregation behind one object so
the match driver does not reimplement it. The aggregator
returns a ``field_name -> Counter(value -> count)`` map ready
to feed the per-thesaurus matching pipeline.

List/tuple values (multi-value fields such as multiselect or tag
lists, stored as JSON arrays in ``metadata.data``) are expanded
one element at a time so the thesaurus-matching LLM sees one
bullet per label instead of one opaque ``str(list)`` blob.
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
        """Return ``field_name -> Counter(value -> count)`` for every row.

        Multi-value fields are stored as JSON arrays; each element is
        counted once per row so the thesaurus-matching LLM sees every
        distinct label rather than one ``str([...])`` blob.
        """
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
        """Record one (name, value) pair, expanding list/tuple values.

        List/tuple elements are recorded as their own distinct values
        so the per-thesaurus matching LLM sees one bullet per label
        (e.g. ``Spain`` and ``Argentina``) rather than the single
        ``"['Spain', 'Argentina']"`` blob that ``str(list)`` would
        yield. ``None`` and empty-string elements inside the list are
        skipped. Scalars and other JSON shapes fall through to the
        original single-record path.
        """
        if not isinstance(name, str) or not name:
            return
        if value in (None, ""):
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                self._record(name, item, field_counters)
            return
        text = str(value).strip()
        if not text:
            return
        field_counters.setdefault(name, Counter())[text] += 1
