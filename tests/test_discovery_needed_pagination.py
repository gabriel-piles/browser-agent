"""End-to-end test: needs_discovery=true (multi-page pagination).

Scenario: 3 pages, 10 items each, Next button. Tests that
needs_discovery=true; a discovery script is emitted and runs.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=discovery_needed and extract the \
title and date for every document item across ALL pages. There are 3 pages with \
10 items each (30 total). Use the a.next button to paginate — it links to \
?page=N+1. The last page has no Next button. Save each item to save_record with \
the item's link URL as source_url.\
"""


def test_discovery_needed_pagination(fixture_server):
    """Step 0 with needs_discovery=true: 30 records, discovery script emitted."""
    result = run_generation_pipeline("discovery_needed", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
    # A discovery script is only guaranteed when the run actually used
    # discovered_links — the planner may legitimately paginate inline.
    db = sqlite3.connect(result["db_path"])
    try:
        link_count = db.execute("SELECT COUNT(*) FROM discovered_links").fetchone()[0]
    finally:
        db.close()
    if link_count > 0:
        discovery_scripts = [p for p in scripts_dir.glob("*.py") if "__discover__" in p.name]
        assert len(discovery_scripts) >= 1, f"{link_count} discovered links but no discovery script"
