"""End-to-end test: parallel_runners=8 (high concurrency).

Reuses the concurrency fixture (50 items). Tests high-concurrency
save_record and DB write contention.
"""

from __future__ import annotations

import sqlite3

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=concurrency and extract the title \
and date for every document item (50 items). Save each item to save_record \
with the item's link URL as source_url. Use parallel processing.\
"""


def test_parallel_runners_8(fixture_server):
    """Step 0 with parallel_runners=8: 50 records, no duplicates."""
    result = run_generation_pipeline("concurrency", PROMPT, fixture_server, parallel_runners=8)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
    db_path = result["db_path"]
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT source_url, COUNT(*) FROM metadata GROUP BY source_url HAVING COUNT(*) > 1").fetchall()
    conn.close()
    assert len(rows) == 0, f"Found {len(rows)} duplicate source_urls"
