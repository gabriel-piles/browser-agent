"""Persist scraped metadata into a shared SQLite store.

Restructured from the vendored ``EMITTED_SAVE_RECORD_BLOCK`` to fix the
``no such table: metadata`` crash on fresh runs. The DB path and task slug
are resolved lazily at call time from env vars or ``__main__.__file__`` so
both the validation runner (env vars) and standalone scripts (``__file__``)
work without globals injection.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

from script_tools._file_utils import _canonical_url


def _resolve_db_path() -> str:
    """Return the SQLite path, preferring the env var, then ``__main__.__file__``."""
    env = os.environ.get("BROWSER_AGENT_SAVE_RECORD_DB_PATH")
    if env:
        return env
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if not main_file:
        raise RuntimeError(
            "save_record cannot resolve the DB path: __main__ has no __file__. "
            "Set BROWSER_AGENT_SAVE_RECORD_DB_PATH to the metadata.db path."
        )
    base = Path(main_file).resolve().parent.parent / "metadata.db"
    try:
        open(base, "a").close()
    except OSError:
        base = Path(main_file).resolve().parent / "metadata.db"
    return str(base)


def _resolve_task_slug() -> str:
    """Return the task slug, preferring the env var, then ``__main__.__file__`` stem."""
    env = os.environ.get("BROWSER_AGENT_TASK_SLUG")
    if env:
        return env
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    if main_file:
        return Path(main_file).resolve().stem
    return "script"


def _ensure_schema(conn) -> None:
    """Create the ``metadata`` table if it does not exist (fixed schema)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS metadata "
        "(source_url TEXT PRIMARY KEY, task_slug TEXT NOT NULL, "
        "scraped_at TEXT NOT NULL, data TEXT NOT NULL)"
    )


def save_record(source_url: str, data: dict) -> None:
    """Persist one entity's metadata into the shared SQLite store.

    Upserts by source_url: re-running the scraper updates existing
    records instead of creating duplicates. The table schema is fixed
    so downstream scripts can query it without knowing which scraper
    produced the data.

    When downloading multiple files per page (PDFs, images), call this
    once per FILE with a content-stable source_url derived from the
    file's own URL — for PDFs use ``f"{page_url}/pdf/{pdf_id}"`` where
    ``pdf_id = pdf_id_for(pdf_url)`` (the same canonicalized hash the
    download helper uses for the filename; import it from
    ``script_tools._file_utils``). NEVER inline
    ``hashlib.sha1(pdf_url.encode())`` — the helper percent-canonicalizes
    the URL first so the percent-encoded and raw-unicode forms of the
    same PDF collapse to one id (and one DB row); the inline hash skips
    that and creates a duplicate. NEVER use a position index
    (``{i}``, ``#row3``): the metadata table keys on source_url, so a
    position-based key makes a re-run with a different scheme create a
    duplicate row for the same file instead of upserting. The on-disk
    filename is derived by the download helper from the file's download
    URL (``<pdf_id_for(url)>.pdf``); read it from the helper's result
    dict (``result["saved_path"]``) and store it in ``data`` as
    ``pdf_id`` / ``pdf_filename``. Keep the human label and type in
    ``pdf_name`` / ``pdf_type``. The path is a pure function of the
    canonical URL so the download helper's existence check means
    "already downloaded this URL".

    Download status — call ``save_record`` for EVERY discovered PDF,
    success OR failure, so a failed download leaves a row that a
    re-run can retry (the URL would otherwise be lost). On success set
    ``download_status="downloaded"`` and ``pdf_filename`` to the
    on-disk name. On failure set ``download_status="failed"``,
    ``pdf_filename=""`` (empty string, not omitted, so downstream code
    can distinguish "discovered but not downloaded" from "never
    discovered"), and ``download_error`` to the exception message. The
    retry-queue query (rule 8a) selects rows where ``download_status``
    is ``"failed"`` OR ``pdf_filename`` is empty; existing rows from
    prior runs that predate this key are treated as already-downloaded
    and skipped (they have a non-empty ``pdf_filename`` and no
    ``download_status``).

    When the task also captures the source HTML of the page where each
    PDF was found (supporting file), store the HTML helper's basename
    in ``data`` as ``html_filename`` (read it from the
    ``save_page_html`` result dict's ``saved_path``). Downstream
    upload code reads ``html_filename`` to attach the HTML as a
    supporting attachment on the same Uwazi entity. Omit the key
    (or set it to ``None``) when no HTML was captured for a row.

    Store the URL of the page whose HTML was saved as
    ``source_page_url`` in the same ``data`` dict (the ``source_url``
    passed to ``save_page_html``), so downstream Uwazi mapping can
    place it on a ``link``-type property. This is the SOURCE PAGE URL,
    never the PDF download URL (``pdf_url``). Omit when no HTML was
    captured.
    """
    source_url = _canonical_url(source_url)
    if isinstance(data, dict):
        _pu = data.get("pdf_url")
        if isinstance(_pu, str):
            data = {**data, "pdf_url": _canonical_url(_pu)}
    db_path = _resolve_db_path()
    task_slug = _resolve_task_slug()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO metadata (source_url, task_slug, scraped_at, data) VALUES (?, ?, ?, ?)",
            (source_url, task_slug, datetime.datetime.now(datetime.UTC).isoformat(), json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def load_failed_downloads() -> list[tuple[str, dict]]:
    """Return rows whose download failed or whose PDF filename is empty.

    Ensures the schema exists FIRST so a fresh run returns ``[]`` instead
    of raising ``OperationalError: no such table: metadata``. The filter
    matches the rule-8a retry semantics: ``download_status == "failed"``
    OR ``not pdf_filename``. Rows predating the keys with a non-empty
    ``pdf_filename`` are skipped (treated as already downloaded).
    """
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        rows = conn.execute("SELECT source_url, data FROM metadata").fetchall()
    finally:
        conn.close()
    pending = []
    for source_url, raw in rows:
        data = json.loads(raw)
        if data.get("download_status") == "failed" or not data.get("pdf_filename"):
            pending.append((source_url, data))
    return pending
