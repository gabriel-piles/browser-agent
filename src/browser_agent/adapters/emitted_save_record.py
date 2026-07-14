"""Self-contained save-record helper inlined into every emitted script.

Both the in-process validation script and the final script the
operator runs from ``data/scripts/`` are self-contained by contract
— they MUST NOT import from this project. When a task extracts
metadata from many pages, the script needs to persist each record
to a shared SQLite store so downstream scripts can query it. This
helper is shipped as a plain-Python string and prepended to every
emitted ``python_code``, mirroring the :mod:`emitted_page_wait`
pattern.

The helper writes one row per page to a fixed-schema ``metadata`` table.
``INSERT OR REPLACE`` keyed on ``source_url`` makes the scraper
crash-resilient (records saved incrementally) and idempotent on re-runs
(no duplicates). For the final emitted script the DB path and task
slug are computed from ``__file__`` at runtime; the in-process
validation runner injects them as ``_SAVE_RECORD_DB_PATH`` /
``_SAVE_RECORD_TASK_SLUG`` globals so validation writes land in the
same SQLite file the final script uses.
"""

from __future__ import annotations


def with_emitted_save_record(python_code: str) -> str:
    """Prepend the vendored save-record helper to ``python_code``.

    Both the in-process validation runner
    (:class:`InProcessScriptRunnerAdapter`) and the final-script
    emit path (``generate_script._emit``) call this so the helper
    appears at the top of every script that runs. The helper is
    idempotent: if the script already contains the block marker it
    is returned unchanged.
    """
    if "BEGIN emitted save-record helper" in python_code:
        return python_code
    return f"{EMITTED_SAVE_RECORD_BLOCK}{python_code}"


# This block is intentionally a single literal string. The
# in-process validation runner and the ``generate_script`` driver
# concatenate it in front of the LLM's emitted code so the script gets a
# real persistence function without importing from this project.
EMITTED_SAVE_RECORD_BLOCK = '''\
# ── BEGIN emitted save-record helper (vendored from browser_agent) ──
import sqlite3
import json
import datetime
from pathlib import Path

_SAVE_RECORD_DB_PATH = str(Path(__file__).resolve().parent.parent / "metadata.db")
try:
    open(_SAVE_RECORD_DB_PATH, "a").close()
except OSError:
    _SAVE_RECORD_DB_PATH = str(Path(__file__).resolve().parent / "metadata.db")
_SAVE_RECORD_TASK_SLUG = Path(__file__).resolve().stem


def save_record(source_url: str, data: dict) -> None:
    """Persist one page's metadata into the shared SQLite store.

    Upserts by source_url: re-running the scraper updates existing
    records instead of creating duplicates. The table schema is fixed
    so downstream scripts can query it without knowing which scraper
    produced the data.
    """
    conn = sqlite3.connect(_SAVE_RECORD_DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata "
            "(source_url TEXT PRIMARY KEY, task_slug TEXT NOT NULL, "
            "scraped_at TEXT NOT NULL, data TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata "
            "(source_url, task_slug, scraped_at, data) VALUES (?, ?, ?, ?)",
            (source_url, _SAVE_RECORD_TASK_SLUG,
             datetime.datetime.now(datetime.UTC).isoformat(),
             json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
# ── END emitted save-record helper ──

'''
