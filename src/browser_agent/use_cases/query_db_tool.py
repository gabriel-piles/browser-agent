"""The ``query_db`` tool bound to the verification agent.

Read-only SQL access to the run's ``metadata.db``. The agent inventories
coverage: which PDF URLs and filenames are recorded, distribution by
subcategory/year/state, etc. Writes are blocked two ways: a soft regex
guard that rejects anything not a single ``SELECT`` statement, and a
hard guard via ``sqlite3`` opened read-only (``mode=ro`` URI).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps

_MAX_ROWS = 200
_SCHEMA_REMINDER = (
    "Schema: metadata(source_url TEXT PRIMARY KEY, task_slug TEXT, scraped_at TEXT, data TEXT); "
    "discovered_links(url TEXT PRIMARY KEY, filter_label TEXT, status TEXT, discovered_at TEXT). "
    "`data` is a JSON blob whose keys include file_url, pdf_filename, "
    "pdf_id, pdf_name, pdf_type, subcategory, year, state. "
    "`discovered_links.status` is 'discovered' (not yet processed), 'processed', or 'sample' (validation seeds, never work items)."
)
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


async def query_db(ctx: RunContext[VerificationAgentDeps], sql_query: str) -> str:
    """Run a read-only SELECT against ``metadata.db`` and return rows.

    Pass a single SELECT statement (no trailing ``;``, no multiple
    statements). Returns up to 200 rows as a pipe-table; if more, a
    truncation footer is appended. ``data`` column values are returned
    verbatim (parse the JSON in a follow-up ``run_read_script`` if you
    need decoded fields).
    """
    deps = ctx.deps
    if deps.query_db_calls >= deps.query_db_limit:
        return _limit_reached(deps)
    async with traced_tool("query_db", summary=sql_query[:120]):
        guard = _guard(sql_query)
        if guard is not None:
            return guard
        rows, columns = _run_select(deps.db_path, sql_query)
        deps.query_db_calls += 1
    return _format(rows, columns, deps.db_path)


def _guard(sql_query: str) -> str | None:
    """Return an error string if the query is not a single SELECT, else None."""
    if not _SELECT_RE.match(sql_query):
        return f"# query_db: rejected — only a single SELECT statement is allowed.\n{_SCHEMA_REMINDER}"
    parts = [p for p in sql_query.split(";") if p.strip()]
    if len(parts) != 1:
        return f"# query_db: rejected — pass exactly one statement with no trailing ';'.\n{_SCHEMA_REMINDER}"
    return None


def _limit_reached(deps: VerificationAgentDeps) -> str:
    return (
        f"# query_db limit reached ({deps.query_db_limit}).\n"
        "You have used all your SQL queries. STOP calling this tool.\n"
        "Use the deterministic reconciler inventory for coverage; "
        "emit the final VerificationReport now."
    )


def _run_select(db_path: Path, sql_query: str) -> tuple[list[tuple[object, ...]], list[str]]:
    """Execute the SELECT read-only and return (rows, column_names)."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute(sql_query)
        rows = cur.fetchmany(_MAX_ROWS + 1)
        columns = [d[0] for d in cur.description] if cur.description else []
        return rows, columns
    finally:
        conn.close()


def _format(rows: list[tuple[object, ...]], columns: list[str], db_path: Path) -> str:
    """Render rows as a pipe-table with a truncation footer when needed."""
    total = len(rows)
    shown = rows[:_MAX_ROWS]
    if not columns:
        return f"# query_db: no columns returned (db={db_path.name}).\n{total} row(s)."
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join([" --- "] * len(columns)) + "|"
    body = ["| " + " | ".join(_cell(c) for c in row) + " |" for row in shown]
    lines = [f"# query_db ({db_path.name})", header, sep, *body]
    if total > _MAX_ROWS:
        lines.append(f"... (truncated, {total} total rows)")
    lines.append(f"\n{_SCHEMA_REMINDER}")
    return "\n".join(lines)


def _cell(value: object) -> str:
    """Render a single cell, replacing newlines/pipes for markdown safety."""
    if value is None:
        return "NULL"
    text = str(value).replace("\n", " ").replace("|", "\\|")
    if len(text) > 120:
        return text[:117] + "..."
    return text
