"""Persist discovered link URLs into the run's ``discovered_links`` table.

Mirrors :mod:`save_record`'s DB-path resolution: the DB path and task
slug resolve lazily from env vars or ``__main__.__file__`` so both the
validation runner (env vars) and standalone emitted scripts (``__file__``)
work. The table lives in the same ``metadata.db`` as the ``metadata``
table; discovery writes here, the processing script reads via
:func:`load_discovered_links`. Idempotent re-runs: ``load_discovered_links``
returns only ``status='discovered'`` rows; :func:`mark_link_processed`
advances them to ``status='processed'``.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import sys
from pathlib import Path

from script_tools._file_utils import _canonical_url


def _resolve_db_path() -> str:
    """Return the SQLite path: env var, else ``<run>/metadata.db`` for emitted scripts."""
    env = os.environ.get("BROWSER_AGENT_SAVE_RECORD_DB_PATH")
    if env:
        return env
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if not main_file:
        raise RuntimeError(
            "discovered_links_store cannot resolve the DB path: __main__ has no __file__. "
            "Set BROWSER_AGENT_SAVE_RECORD_DB_PATH to the metadata.db path."
        )
    script_path = Path(main_file).resolve()
    if script_path.parent.name != "scripts":
        raise RuntimeError(
            "discovered_links_store cannot resolve a safe DB path: the running script is not "
            "under a <run>/scripts/ directory. Set BROWSER_AGENT_SAVE_RECORD_DB_PATH to "
            f"the metadata.db path (__main__.__file__ = {main_file!r})."
        )
    return str(script_path.parent.parent / "metadata.db")


def _ensure_schema(conn) -> None:
    """Create the ``discovered_links`` table if it does not exist."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS discovered_links "
        "(url TEXT PRIMARY KEY, filter_label TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'discovered', discovered_at TEXT NOT NULL)"
    )


def save_discovered_link(url: str, filter_label: str = "") -> None:
    """Insert a discovered link URL into ``discovered_links`` (idempotent)."""
    canon = _canonical_url(url)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(_resolve_db_path(), timeout=5)
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO discovered_links (url, filter_label, status, discovered_at) "
            "VALUES (?, ?, 'discovered', ?)",
            (canon, filter_label, now),
        )
        conn.commit()
    finally:
        conn.close()


def load_discovered_links() -> list[tuple[str, str]]:
    """Return ``[(url, filter_label)]`` rows not yet processed (status='discovered')."""
    conn = sqlite3.connect(_resolve_db_path(), timeout=5)
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT url, filter_label FROM discovered_links WHERE status='discovered'").fetchall()
        return rows
    finally:
        conn.close()


def mark_link_processed(url: str) -> None:
    """Set ``status='processed'`` so re-runs skip already-handled links."""
    canon = _canonical_url(url)
    conn = sqlite3.connect(_resolve_db_path(), timeout=5)
    try:
        _ensure_schema(conn)
        conn.execute("UPDATE discovered_links SET status='processed' WHERE url=?", (canon,))
        conn.commit()
    finally:
        conn.close()
