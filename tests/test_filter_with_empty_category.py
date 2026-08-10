"""End-to-end test: filter value with zero results.

Scenario: 4 categories; one ("archived") returns 0 items. Other 3
return 5 each (15 total). Tests that the agent handles empty filter
results without crashing.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=filter_empty and extract the \
title and category for every document item across ALL filter values. There are \
4 categories in a <select> dropdown: reports, resolutions, measures, and \
archived. The "archived" category returns 0 items — handle this gracefully \
without crashing. The other 3 categories have 5 items each (15 total). Iterate \
all 4 filter values. Save each item to save_record with the item's link URL \
as source_url and include the category in the data.\
"""


def test_filter_with_empty_category(fixture_server):
    """Step 0 handles empty filter result: 15 records, no crash."""
    result = run_generation_pipeline("filter_empty", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 15)
    assert_fields_non_null(result, ["title", "category"])
