"""End-to-end test: single-page static list extraction.

Scenario: One page with 10 items, each in a div with h3 title,
date, author, and a link. No pagination, no filters, no PDFs.

Tests: basic extraction, save_record with unique core_id per
item, CSS selectors, field non-null checks.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_page_list and extract the title, date, \
and author for every document item on the page. Each item is in a div.item with \
an h3 a link, a span.date, and a span.author. Save each item to save_record with \
the item's link URL as the core_id. There are 10 items on a single page — \
do NOT paginate, scroll, or click anything.\
"""


def test_single_page_list(fixture_server):
    """Step 0 produces a script that extracts all 10 items with unique core_ids."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
