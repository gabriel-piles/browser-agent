"""End-to-end test: some items lack optional fields.

Scenario: 10 items; items 1-5 have title + date + author; items 6-10
have only title + date (no author). Tests graceful handling of
missing optional fields.
"""

from __future__ import annotations

import json
import sqlite3

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=missing_fields and extract the \
title, date, and author for every document item (10 items). Some items may not \
have an author — for those, set author to null or empty string. Do NOT crash on \
missing elements. Save each item to save_record with the item's link URL as \
core_id.\
"""


def test_missing_fields(fixture_server):
    """Step 0 handles missing fields: 10 records, author present in >=5, null in >=5."""
    result = run_generation_pipeline("missing_fields", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
    # Verify author is non-null in >=5 rows and null/empty in >=5 rows
    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT data FROM metadata").fetchall()
        conn.close()
        author_present = 0
        author_absent = 0
        for (data_json,) in rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            author = data.get("author")
            if author is not None and str(author).strip() != "":
                author_present += 1
            else:
                author_absent += 1
        assert author_present >= 5, f"Author present in only {author_present} rows, expected >=5"
        assert author_absent >= 5, f"Author absent in only {author_absent} rows, expected >=5"
