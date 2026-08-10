"""End-to-end test: multi-page pagination via Next button.

Scenario: 5 pages with 10 items each (50 total). Each page has a
Next button linking to ?page=N+1. Last page has no Next button.

Tests: pagination loop, link collection across pages, save_record
with unique source_url per item, termination on missing Next button.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=multi_page_pagination and extract the \
title and date for every document item across ALL pages. There are 5 pages with \
10 items each (50 total). Use the a.next button to paginate — it links to \
?page=N+1. The last page has no Next button. Save each item to save_record with \
the item's link URL (urljoin of the page URL and the href) as the source_url.\
"""


def test_multi_page_pagination(fixture_server):
    """Step 0 produces a script that paginates through all 5 pages and extracts 50 items."""
    result = run_generation_pipeline("multi_page_pagination", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
