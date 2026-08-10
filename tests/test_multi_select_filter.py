"""End-to-end test: multiple independent filters.

Scenario: Two <select> dropdowns: category (3 values) and year (3
values). 9 combinations, 5 items each (45 total). Tests that the
agent iterates the Cartesian product of both filters.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=multi_filter and extract the \
title, category, and year for every document item. There are TWO <select> \
dropdowns: "category" (3 values: reports, resolutions, measures) and "year" \
(3 values: 2022, 2023, 2024). Iterate ALL 9 combinations of both filters (the \
Cartesian product). Each combination has 5 items (45 total). Save each item to \
save_record with the item's link URL as source_url and include the category \
and year in the data.\
"""


def test_multi_select_filter(fixture_server):
    """Step 0 iterates Cartesian product of 2 filters: 45 records."""
    result = run_generation_pipeline("multi_filter", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 45)
    assert_fields_non_null(result, ["title", "category", "year"])
