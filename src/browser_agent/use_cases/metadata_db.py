"""Shared read access to the per-run ``metadata.db`` SQLite store.

Both the apply pipeline (:mod:`apply_mapping_use_case`) and the
catalog builder (:mod:`metadata_catalog_builder`) read the same
fixed-schema ``metadata`` table. Centralising the query + JSON decode
keeps the two in sync and gives one place to evolve the schema.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from pathlib import Path


def ensure_metadata_schema(db_path: Path) -> None:
    """Create the run's ``metadata.db`` with the fixed schema if absent.

    Idempotent; called at flow start so verification can open the DB
    read-only even when a subtask's script saved zero records (no
    ``save_record`` call ever created the file). Keep the two DDLs in
    sync with ``script_tools/save_record.py`` and
    ``script_tools/discovered_links_store.py``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata "
            "(source_url TEXT PRIMARY KEY, task_slug TEXT NOT NULL, "
            "scraped_at TEXT NOT NULL, data TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS discovered_links "
            "(url TEXT PRIMARY KEY, filter_label TEXT NOT NULL DEFAULT '', "
            "status TEXT NOT NULL DEFAULT 'discovered', discovered_at TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def query_rows(db_path: Path, run: str | None = None) -> list[tuple[str, str, str]]:
    """Return ``(source_url, task_slug, data_json)`` rows from ``metadata.db``.

    When ``run`` is not None the rows are filtered by ``task_slug``;
    pass None to read every row in the table.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        if run is not None:
            return conn.execute(
                "SELECT source_url, task_slug, data FROM metadata WHERE task_slug = ?",
                (run,),
            ).fetchall()
        return conn.execute("SELECT source_url, task_slug, data FROM metadata").fetchall()
    finally:
        conn.close()


def count_discovered_links(db_path: Path) -> int:
    """Count rows in ``discovered_links``; 0 when the file/table is missing."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM discovered_links").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def discovered_link_counts(db_path: Path) -> dict[str, int]:
    """Map ``filter_label`` to row count in ``discovered_links``; {} when missing."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT filter_label, COUNT(*) FROM discovered_links GROUP BY filter_label").fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {label: count for label, count in rows}


def parse_row_data(raw: str | None) -> dict[str, Any]:
    """Decode the ``metadata.data`` JSON blob of one row, returning ``{}`` on failure."""
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
