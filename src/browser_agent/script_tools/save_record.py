"""Persist scraped metadata into a shared SQLite store.

The DB path and task slug are resolved lazily at call time from env vars
or ``__main__.__file__`` so both the validation runner (env vars) and
standalone scripts (``__file__``) work without globals injection. When
the env var is unset the DB is only ever written next to a real emitted
script under ``<run>/scripts/`` (i.e. ``<run>/metadata.db``); it is
never written into the source tree.
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
    """Return the SQLite path: env var, else ``<run>/metadata.db`` for emitted scripts."""
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
    script_path = Path(main_file).resolve()
    # Only write metadata.db next to the run dir for a real emitted script
    # under <run>/scripts/. Never derive it from an arbitrary __main__
    # location (e.g. the step-0 driver in src/browser_agent/) — that would
    # drop a stray metadata.db into the source tree.
    if script_path.parent.name != "scripts":
        raise RuntimeError(
            "save_record cannot resolve a safe DB path: the running script is not "
            "under a <run>/scripts/ directory. Set BROWSER_AGENT_SAVE_RECORD_DB_PATH to "
            f"the metadata.db path (__main__.__file__ = {main_file!r})."
        )
    return str(script_path.parent.parent / "metadata.db")


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
        "(core_id TEXT PRIMARY KEY, core_task_slug TEXT NOT NULL, "
        "scraped_at TEXT NOT NULL, data TEXT NOT NULL)"
    )


def save_record(core_id: str, data: dict) -> None:
    """Persist one entity's metadata into the shared SQLite store.

    Upserts by core_id: re-running the scraper updates existing
    records instead of creating duplicates. The table schema is fixed
    so downstream scripts can query it without knowing which scraper
    produced the data. Keys prefixed ``core_`` are agent-instrumented
    (download discipline, HTML capture); all other keys come from the
    task prompt's extraction spec.

    When downloading multiple files per page (PDFs, images), call this
    once per FILE with a content-stable core_id derived from the
    file's own URL — for PDFs use ``f"{page_url}/pdf/{pdf_id}"`` where
    ``pdf_id = pdf_id_for(file_url)`` (the same canonicalized hash the
    download helper uses for the filename; import it from
    ``script_tools._file_utils``). NEVER inline
    ``hashlib.sha1(file_url.encode())`` — the helper percent-canonicalizes
    the URL first so the percent-encoded and raw-unicode forms of the
    same PDF collapse to one id (and one DB row); the inline hash skips
    that and creates a duplicate. NEVER use a position index
    (``{i}``, ``#row3``): the metadata table keys on core_id, so a
    position-based key makes a re-run with a different scheme create a
    duplicate row for the same file instead of upserting. The on-disk
    filename is derived by the download helper from the file's download
    URL (``<pdf_id_for(url)>.pdf``); read it from the helper's result
    dict (``result["saved_path"]``) and store it in ``data`` as
    ``core_pdf_filename``. Keep the human label and
    type in ``core_pdf_name`` / ``core_pdf_type``. The path is a pure
    function of the canonical URL so the download helper's existence
    check means "already downloaded this URL".

    Download status — call ``save_record`` for EVERY discovered PDF,
    success OR failure, so a failed download leaves a row that a
    re-run can retry (the URL would otherwise be lost). On success set
    ``core_download_status="downloaded"`` and ``core_pdf_filename`` to
    the on-disk name. On failure set ``core_download_status="failed"``,
    ``core_pdf_filename=""`` (empty string, not omitted, so downstream
    code can distinguish "discovered but not downloaded" from "never
    discovered"), and ``core_download_error`` to the exception message.
    The retry-queue query (rule 8a) selects rows where
    ``core_download_status`` is ``"failed"`` OR ``core_pdf_filename``
    is empty; rows lacking ``core_pdf_filename`` (e.g. legacy runs from
    before the ``core_`` prefix) are treated as not-downloaded and
    retried. For a page whose metadata rendered but that has no
    downloadable files of its own (rule 14b), save ONE metadata-only
    row keyed by the page URL with ``core_download_status="no_files"``
    and ``core_pdf_filename=""`` — the retry queue skips ``no_files``
    rows.

    ``core_pdf_filename`` holds the downloaded file's on-disk basename
    for EVERY downloaded file — PDF or non-PDF document (``.doc``/``.docx``/
    ``.rtf``/…); there is no separate supporting role. Never set
    ``download_role`` or ``supporting_filename``; the download helper
    derives the basename (``Path(result["saved_path"]).name``) — store it
    verbatim. "Whether a file is a PDF or a supporting document" is
    decided later, at Uwazi upload time, from the file's extension.

    When the task also captures the source HTML of the page where each
    PDF was found (supporting file), store the HTML helper's basename
    in ``data`` as ``core_html_filename`` (read it from the
    ``save_page_html`` result dict's ``saved_path``). Downstream
    upload code reads ``core_html_filename`` to attach the HTML as a
    supporting attachment on the same Uwazi entity. Omit the key
    (or set it to ``None``) when no HTML was captured for a row.

    Store the URL of the page whose HTML was saved as
    ``core_source_page_url`` in the same ``data`` dict (the
    ``source_url`` passed to ``save_page_html``), so downstream Uwazi
    mapping can place it on a ``link``-type property. This is the
    SOURCE PAGE URL, never the file download URL (``core_file_url``).
    Omit when no HTML was captured.
    """
    core_id = _canonical_url(core_id)
    canonical_file_url = ""
    if isinstance(data, dict):
        _pu = data.get("core_file_url")
        if isinstance(_pu, str):
            canonical_file_url = _canonical_url(_pu)
            data = {**data, "core_file_url": canonical_file_url}
    db_path = _resolve_db_path()
    task_slug = _resolve_task_slug()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        if canonical_file_url:
            existing = conn.execute(
                "SELECT core_id FROM metadata WHERE json_extract(data, '$.core_file_url') = ? LIMIT 1",
                (canonical_file_url,),
            ).fetchone()
            if existing:
                core_id = existing[0]
        conn.execute(
            "INSERT OR REPLACE INTO metadata (core_id, core_task_slug, scraped_at, data) VALUES (?, ?, ?, ?)",
            (core_id, task_slug, datetime.datetime.now(datetime.UTC).isoformat(), json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def load_failed_downloads() -> list[tuple[str, dict]]:
    """Return rows whose download failed or whose PDF filename is empty.

    Ensures the schema exists FIRST so a fresh run returns ``[]`` instead
    of raising ``OperationalError: no such table: metadata``. The filter
    matches the rule-8a retry semantics: ``core_download_status ==
    "failed"`` OR ``not core_pdf_filename``. Rows lacking
    ``core_pdf_filename`` (legacy pre-``core_`` runs) are treated as
    not-downloaded. Rows with ``core_download_status == "no_files"``
    (metadata-only pages, rule 14b) are excluded — there is nothing to
    retry.
    """
    db_path = _resolve_db_path()
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(conn)
        rows = conn.execute("SELECT core_id, data FROM metadata").fetchall()
    finally:
        conn.close()
    pending = []
    for core_id, raw in rows:
        data = json.loads(raw)
        if data.get("core_download_status") == "no_files":
            continue
        if data.get("core_download_status") == "failed" or not data.get("core_pdf_filename"):
            pending.append((core_id, data))
    return pending
