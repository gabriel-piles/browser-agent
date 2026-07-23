"""Self-contained save-record helper inlined into every emitted script.

Both the in-process validation script and the final script the
operator runs from ``data/scripts/`` are self-contained by contract
— they MUST NOT import from this project. When a task extracts
metadata from many pages, the script needs to persist each record
to a shared SQLite store so downstream scripts can query it. This
helper is shipped as a plain-Python string and prepended to every
emitted ``python_code``, mirroring the :mod:`emitted_page_wait`
pattern.

The helper writes one row per scraped entity to a fixed-schema
``metadata`` table. ``INSERT OR REPLACE`` keyed on ``source_url``
makes the scraper crash-resilient (records saved incrementally) and
idempotent on re-runs (no duplicates).

When the task downloads multiple files (PDFs, images) per page, the
script MUST call ``save_record`` once per FILE with a unique
``source_url`` (e.g. ``f"{page_url}/pdf/{pdf_idx}"``) so each file
gets its own row. The on-disk filename is derived by the download
helper from the file's download URL (``pdf_{sha1(url)[:12]}.pdf``),
never a human label or a position index — labels collide across
pages, and position indices break the download helper's skip-by-path
when result order changes. The caller reads the actual filename from
the helper's result dict (``result["saved_path"]``) and stores it in
the ``data`` dict (``pdf_filename``), alongside the human-readable
name and document type (``pdf_name``, ``pdf_type``, ``pdf_id``,
``pdf_url``) so downstream code joins file to metadata without
parsing the path. When the task also captures the page HTML as a
supporting file, the HTML basename is stored under ``html_filename``
(read from the ``save_page_html`` helper's ``saved_path``).
Path resolution:

* For final scripts the DB path and task slug are computed from
  ``__file__`` at runtime. Because final scripts live under
  ``data/runs/<run>/scripts/``, resolving ``.. / .. / metadata.db``
  lands inside the runner folder.
* The in-process validation runner injects ``_SAVE_RECORD_DB_PATH`` /
  ``_SAVE_RECORD_TASK_SLUG`` globals before executing the helper, so
  validation writes land in the same SQLite file the final script uses
  and never leak a database outside the runner folder.
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

# The in-process validation runner injects these globals so the
# metadata database is always written inside the runner folder. When
# they are not present, this is a standalone final script and we fall
# back to a path derived from its location under ``<run>/scripts/``.
if "_SAVE_RECORD_DB_PATH" not in globals():
    _SAVE_RECORD_DB_PATH = str(Path(__file__).resolve().parent.parent / "metadata.db")
    try:
        open(_SAVE_RECORD_DB_PATH, "a").close()
    except OSError:
        _SAVE_RECORD_DB_PATH = str(Path(__file__).resolve().parent / "metadata.db")
if "_SAVE_RECORD_TASK_SLUG" not in globals():
    _SAVE_RECORD_TASK_SLUG = Path(__file__).resolve().stem


def save_record(source_url: str, data: dict) -> None:
    """Persist one entity's metadata into the shared SQLite store.

    Upserts by source_url: re-running the scraper updates existing
    records instead of creating duplicates. The table schema is fixed
    so downstream scripts can query it without knowing which scraper
    produced the data.

    When downloading multiple files per page (PDFs, images), call this
    once per FILE with a unique source_url (e.g. ``f"{page_url}/pdf/{i}"``)
    so each file gets its own row. The on-disk filename is derived by
    the download helper from the file's download URL
    (``pdf_{sha1(url)[:12]}.pdf``); read it from the helper's result
    dict (``result["saved_path"]``) and store it in ``data`` as
    ``pdf_id`` / ``pdf_filename``. Keep the human label and type in
    ``pdf_name`` / ``pdf_type``. The path is a pure function of the URL
    so the download helper's existence check means "already downloaded
    this URL".

    When the task also captures the source HTML of the page where each
    PDF was found (supporting file), store the HTML helper's basename
    in ``data`` as ``html_filename`` (read it from the
    ``save_page_html`` result dict's ``saved_path``). Downstream
    upload code reads ``html_filename`` to attach the HTML as a
    supporting attachment on the same Uwazi entity. Omit the key
    (or set it to ``None``) when no HTML was captured for a row.
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
