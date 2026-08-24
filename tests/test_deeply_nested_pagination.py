"""End-to-end test: 20 pages of pagination.

Scenario: 20 pages, 5 items each (100 total). Next button on every
page except the last. Tests long pagination loop.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=deep_pagination and extract the \
title and date for every document item across ALL pages. There are 20 pages \
with 5 items each (100 total). Use the a.next button to paginate through ALL \
pages — the last page has no Next button. Save each item to save_record with \
the item's link URL as core_id.\
"""


def test_deeply_nested_pagination(fixture_server):
    """Step 0 handles 20-page pagination: 100 records."""
    result = run_generation_pipeline("deep_pagination", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 100)
    assert_fields_non_null(result, ["title", "date"])
