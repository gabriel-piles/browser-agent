"""End-to-end test: filter + pagination combined.

Scenario: 2 categories, 3 pages each, 5 items per page (30 total).
Filter changes reset to page 1. Tests filter+pagination nesting.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=filter_pagination and extract the \
title, category, and date for every document item. There are 2 categories in a \
<select> dropdown: reports and resolutions. Each category has 3 pages with 5 \
items per page (30 total). Iterate both categories, paginating within each. \
Changing the filter resets to page 1. Save each item to save_record with the \
item's link URL as core_id and include the category.\
"""


def test_mixed_pagination_and_filter(fixture_server):
    """Step 0 handles filter+pagination: 30 records."""
    result = run_generation_pipeline("filter_pagination", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
