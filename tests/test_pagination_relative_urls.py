"""End-to-end test: relative URL construction.

Scenario: 3 pages; Next button href is page2.html (relative). Detail
links are doc/N (relative). Tests that the agent uses urljoin to
construct absolute URLs.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=relative_urls and extract the \
title and date for every document item across ALL pages. There are 3 pages with \
10 items each (30 total). The Next button uses RELATIVE hrefs (e.g. \
"page2.html?scenario=relative_urls") — use urljoin to construct absolute URLs. \
Save each item to save_record with the item's ABSOLUTE link URL as core_id.\
"""


def test_pagination_relative_urls(fixture_server):
    """Step 0 handles relative URLs: 30 records with absolute core_id."""
    result = run_generation_pipeline("relative_urls", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
    # Verify all core_id values are absolute
    import sqlite3

    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT core_id FROM metadata").fetchall()
        conn.close()
        for (url,) in rows:
            assert url.startswith("http://127.0.0.1"), f"core_id not absolute: {url}"
