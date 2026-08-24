"""Terminal viewer for the active run's metadata.db.

Run: python -m browser_agent.drivers.view_metadata
Type a pdf_filename (or a substring) at the prompt to see its full
metadata card. Empty input exits. Enter '?' to list all pdf_filename
values; '*' to dump every row's summary.
"""

import json
import sqlite3
from pathlib import Path

from browser_agent.adapters.runs_config_loader import RunsConfigLoader


_PATH_CACHE: Path | None = None


def _db_path() -> Path:
    global _PATH_CACHE
    if _PATH_CACHE is None:
        _PATH_CACHE = RunsConfigLoader.resolve_active_path() / "metadata.db"
    return _PATH_CACHE


SUBSTR_PROMPT = "core_pdf_filename (or ? to list, * to dump all, empty to quit) > "
URL_KEYS = {"core_id", "core_file_url", "source_page", "core_source_page_url"}
SINGLE_LINE_KEYS = URL_KEYS | {k for k in ()}


def connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise SystemExit(f"DB not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def all_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT core_id, task_slug, scraped_at, data FROM metadata ORDER BY scraped_at").fetchall()


def parsed(row: sqlite3.Row) -> dict:
    data = json.loads(row["data"]) if row["data"] else {}
    return {"core_id": row["core_id"], "task_slug": row["task_slug"], "scraped_at": row["scraped_at"], **data}


def is_url_key(key: str) -> bool:
    return key in URL_KEYS or key.endswith("_url") or key.endswith("_page")


def header(text: str, width: int = 64) -> str:
    line = "─" * (width - 2)
    return f"┌{line}┐\n│ {text:<{width - 3}}│\n└{line}┘"


def render_card(record: dict, width: int = 64) -> str:
    rows = [header(f"core_pdf_filename = {record.get('core_pdf_filename', '<missing>')}", width)]
    keys = [
        "core_id",
        "task_slug",
        "scraped_at",
        "core_pdf_id",
        "core_pdf_name",
        "core_pdf_type",
        "core_file_url",
        "core_pdf_filename",
        "core_html_filename",
        "source_page",
        "core_source_page_url",
    ]
    for k in keys:
        if k in record and record[k] not in (None, ""):
            rows.append(render_field(k, str(record[k]), width))
    extras = sorted(set(record) - set(keys))
    for k in extras:
        v = record[k]
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        rows.append(render_field(k, text, width))
    rows.append("└" + "─" * (width - 2) + "┘")
    return "\n".join(rows)


def render_field(key: str, value: str, width: int) -> str:
    if is_url_key(key):
        return f"│ {key:<14} : {value}"
    return render_kv_boxed(key, value, width)


def render_kv_boxed(key: str, value: str, width: int) -> str:
    inner = width - 6
    val_lines = [value[i : i + inner] for i in range(0, len(value), inner)] or [""]
    out = [f"│ {key:<14} │ {val_lines[0]:<{inner}} │"]
    for extra in val_lines[1:]:
        out.append(f"│ {' ' * 14} │ {extra:<{inner}} │")
    out.append(f"│ {'─' * 14} ┼ {'─' * inner} │")
    return "\n".join(out)


def list_filenames(conn: sqlite3.Connection) -> None:
    seen = []
    for row in all_rows(conn):
        fn = parsed(row).get("core_pdf_filename")
        if fn and fn not in seen:
            seen.append(fn)
    print(f"\n{len(seen)} core_pdf_filename values:\n")
    for fn in seen:
        print(f"  • {fn}")
    print()


def dump_all(conn: sqlite3.Connection) -> None:
    rows = all_rows(conn)
    print(f"\n{len(rows)} rows total\n")
    for row in rows:
        print(render_card(parsed(row)))
        print()


def find(conn: sqlite3.Connection, query: str) -> list[dict]:
    out = []
    for row in all_rows(conn):
        rec = parsed(row)
        if query.lower() in rec.get("core_pdf_filename", "").lower():
            out.append(rec)
    return out


def summary(conn: sqlite3.Connection) -> None:
    rows = all_rows(conn)
    slugs = sorted({r["task_slug"] for r in rows})
    with_fn = sum(1 for r in rows if parsed(r).get("core_pdf_filename"))
    print(header(f"metadata.db — {len(rows)} rows", 64))
    print(f"  task_slugs : {', '.join(slugs)}")
    print(f"  rows with core_pdf_filename : {with_fn}")
    print(f"  DB path    : {_db_path()}\n")


def main() -> None:
    conn = connect()
    try:
        summary(conn)
        for row in all_rows(conn)[:2]:
            print(render_card(parsed(row)))
            print()
        while True:
            q = input(SUBSTR_PROMPT).strip()
            if not q:
                break
            if q == "?":
                list_filenames(conn)
                continue
            if q == "*":
                dump_all(conn)
                continue
            matches = find(conn, q)
            if not matches:
                print(f"  no rows match '{q}'\n")
                continue
            print(f"\n{len(matches)} match(es) for '{q}':\n")
            for rec in matches:
                print(render_card(rec))
                print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
