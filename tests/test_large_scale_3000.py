"""End-to-end test: large-scale 3000 documents with difficult navigation.

Scenario: 3000 documents across 150 pages (20 items per page).
Pagination uses a dropdown + Previous/Next buttons. Tests that
the agent generates a script that handles large-scale pagination
correctly — using the Next button to iterate through pages and
save_record per item.

Since scraping all 3000 items takes too long for a test, we verify
that the agent's script paginates correctly and saves at least
100 records (5 pages worth) within the timeout.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=large_scale_3000 and extract the \
title and date for every document item from the LISTING PAGES directly. There are \
3000 documents across 150 pages (20 items per page). The title and date are visible \
on the listing page — do NOT navigate to each detail page. Use the a.next button to \
paginate through ALL pages — it links to ?p=N+1. The last page has no Next button \
(it is disabled). Save each item to save_record with the item's link URL \
(urljoin of page URL and href) as source_url. This is a large scrape — \
persist records as you go (save_record per item, not batch at the end).\
"""


def test_large_scale_3000(fixture_server):
    """Step 0 handles 3000-doc pagination: generates a script that paginates and saves records."""
    result = run_generation_pipeline("large_scale_3000", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 100)
    assert_fields_non_null(result, ["title", "date"])
