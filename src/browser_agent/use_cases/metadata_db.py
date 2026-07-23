"""Shared read access to the per-run ``metadata.db`` SQLite store.

Both the apply pipeline (:mod:`apply_mapping_use_case`) and the
catalog builder (:mod:`metadata_catalog_builder`) read the same
fixed-schema ``metadata`` table. Centralising the query + JSON decode
keeps the two in sync and gives one place to evolve the schema.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def query_rows(db_path: Path, run: str | None = None) -> list[tuple[str, str, str]]:
    """Return ``(source_url, task_slug, data_json)`` rows from ``metadata.db``.

    When ``run`` is not None the rows are filtered by ``task_slug``;
    pass None to read every row in the table.
    """
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


def parse_row_data(raw: str | None) -> dict:
    """Decode the ``metadata.data`` JSON blob of one row, returning ``{}`` on failure."""
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
