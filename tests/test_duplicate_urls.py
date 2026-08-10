"""End-to-end test: same URL on multiple pages (deduplication).

Scenario: 3 pages; item #5 on page 1 also appears on page 2 (same
URL). 28 unique items, 30 total links. Tests save_record dedup via
source_url unique constraint.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=duplicate_urls and extract the \
title and date for every document item from the LISTING PAGES directly. There \
are 3 pages with 10 items each — each item has an h3 a link and a span.date. \
One URL appears on two different pages, so there are 28 unique items out of 30 \
links. Use the a.next button to paginate — it links to ?page=N+1. Extract the \
title and date from the listing page (not detail pages). Deduplicate by \
source_url. Save each UNIQUE item to save_record with the item's link URL \
as source_url. Use HTTP (not HTTPS) for all URLs.\
"""


def test_duplicate_urls(fixture_server):
    """Step 0 deduplicates by source_url: 28 unique records."""
    result = run_generation_pipeline("duplicate_urls", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 28)
    assert_fields_non_null(result, ["title", "date"])
    # Verify no duplicate source_url
    db_path = result["db_path"]
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT source_url, COUNT(*) FROM metadata GROUP BY source_url HAVING COUNT(*) > 1").fetchall()
        conn.close()
        assert len(rows) == 0, f"Found {len(rows)} duplicate source_urls"
