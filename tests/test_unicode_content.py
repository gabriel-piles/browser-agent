"""End-to-end test: non-ASCII text (accents, CJK, emoji).

Scenario: 10 items with titles containing accented characters (café,
naïve), CJK (日本語), and emoji (📄). Tests UTF-8 handling.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=unicode_content and extract the \
title and date for every document item (10 items). Titles contain non-ASCII \
characters (accents, CJK, emoji). Extract the Unicode text correctly — no \
mojibake. Save each item to save_record with the item's link URL as core_id.\
"""


def test_unicode_content(fixture_server):
    """Step 0 handles Unicode: 10 records, correct non-ASCII text."""
    result = run_generation_pipeline("unicode_content", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title"])
    # Verify at least one title contains a non-ASCII character
    import json
    import sqlite3

    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT data FROM metadata").fetchall()
        conn.close()
        found_non_ascii = False
        for (data_json,) in rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            title = data.get("title", "")
            if any(ord(c) > 127 for c in str(title)):
                found_non_ascii = True
                break
        assert found_non_ascii, "No title contains non-ASCII characters"
