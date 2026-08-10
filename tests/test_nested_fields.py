"""End-to-end test: nested data in save_record.

Scenario: 10 items; each has title and nested metadata (author, date,
tags: [tag1, tag2]). Tests that the agent extracts nested fields.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=nested_fields and extract the \
title, author, date, and tags for every document item (10 items). Each item has \
a title, a span.author, a span.date, and a span.tags containing comma-separated \
tags. Store the tags as a list in the save_record data. Save each item to \
save_record with the item's link URL as source_url.\
"""


def test_nested_fields(fixture_server):
    """Step 0 extracts nested fields: 10 records, tags as list."""
    result = run_generation_pipeline("nested_fields", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "author", "date"])
    # Verify tags is a list in at least one row
    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT data FROM metadata").fetchall()
        conn.close()
        found_tags_list = False
        for (data_json,) in rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            tags = data.get("tags")
            if isinstance(tags, list):
                found_tags_list = True
                break
        assert found_tags_list, "No row has tags as a list"
