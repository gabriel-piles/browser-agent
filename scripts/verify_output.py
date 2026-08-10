"""Verify emitted-script output against a scenario's ExpectedOutput.

After the generation pipeline runs and the emitted script executes,
this module checks:
1. ``metadata.db`` exists and has enough rows (``min_records``).
2. Required fields are non-null in at least one row's ``data`` JSON.
3. Enough PDF files in ``downloads/`` (``pdf_count``).

The DB schema is ``metadata(source_url TEXT PK, task_slug TEXT,
scraped_at TEXT, data TEXT)`` where ``data`` is a JSON blob of
scraped fields. See ``script_tools/save_record.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from browser_agent.domain.expected_output import ExpectedOutput
from browser_agent.domain.scenario_result import ScenarioResult


def verify(
    scenario_name: str,
    expected: ExpectedOutput,
    run_path: Path,
    smoke_output: str,
    driver_exit_code: int,
    emitted_script_path: str | None,
) -> ScenarioResult:
    """Check ``run_path`` against ``expected`` and return a ScenarioResult."""
    db_path = run_path / "metadata.db"
    record_count = _record_count(db_path)
    pdf_count = _pdf_count(run_path)
    failures: list[str] = []
    if driver_exit_code != 0:
        failures.append(f"Driver exited with code {driver_exit_code}")
    if record_count < expected.min_records:
        failures.append(f"Expected >={expected.min_records} records, got {record_count}")
    failures.extend(_check_fields(db_path, expected.required_fields))
    if pdf_count < expected.pdf_count:
        failures.append(f"Expected >={expected.pdf_count} PDFs, got {pdf_count}")
    return ScenarioResult(
        scenario_name=scenario_name,
        success=not failures,
        failures=failures,
        emitted_script_path=emitted_script_path,
        smoke_output=smoke_output,
        driver_exit_code=driver_exit_code,
        metadata_db_path=str(db_path) if db_path.exists() else None,
        pdf_count=pdf_count,
        record_count=record_count,
    )


def _record_count(db_path: Path) -> int:
    """Return row count in the metadata table (0 if DB missing)."""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM metadata").fetchone()
        conn.close()
        return rows[0] if rows else 0
    except sqlite3.DatabaseError:
        return 0


def _pdf_count(run_path: Path) -> int:
    """Return the number of .pdf files under the run's downloads/ dir."""
    pdf_dir = run_path / "downloads"
    if not pdf_dir.is_dir():
        return 0
    return len(list(pdf_dir.glob("*.pdf")))


def _check_fields(db_path: Path, required_fields: list[str]) -> list[str]:
    """Return failure messages for required fields that are null in all rows."""
    if not required_fields or not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT data FROM metadata").fetchall()
        conn.close()
    except sqlite3.DatabaseError:
        return [f"Could not read metadata.db to check fields {required_fields}"]
    failures: list[str] = []
    for field in required_fields:
        if _field_non_null(rows, field):
            continue
        failures.append(f"Field '{field}' is null/empty in all rows")
    return failures


def _field_non_null(rows: list[tuple[str, ...]], field: str) -> bool:
    """True when ``field`` is non-empty in at least one row's data JSON."""
    for (data_json,) in rows:
        try:
            data = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            continue
        val = data.get(field)
        if val is not None and str(val).strip() != "":
            return True
    return False
