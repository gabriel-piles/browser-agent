"""End-to-end test: very long titles/descriptions.

Scenario: 5 items; each title is 500 chars, each description is
2000 chars. Tests text extraction completeness.
"""

from __future__ import annotations

import json
import sqlite3

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=long_text and extract the title \
and description for every document item (5 items). Each item has a very long \
title (500 chars) and description (2000 chars). Extract the FULL text without \
truncation. Save each item to save_record with the item's link URL as core_id.\
"""


def test_long_text_fields(fixture_server):
    """Step 0 extracts long text: 5 records, title length >= 500."""
    result = run_generation_pipeline("long_text", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    # Verify at least one title has length >= 500
    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT data FROM metadata").fetchall()
        conn.close()
        found_long = False
        for (data_json,) in rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            title = data.get("title", "")
            if len(str(title)) >= 500:
                found_long = True
                break
        assert found_long, "No title has length >= 500"
