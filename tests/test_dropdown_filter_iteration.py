"""End-to-end test: dropdown filter iteration.

Scenario: A <select> with 4 categories (reports/resolutions/measures/
decisions), 5 items each (20 total). Changing the filter navigates
to ?category=value. Tests that the agent iterates all filter values.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=dropdown_filter and extract the \
title and category for every document item across ALL filter values. There is \
a <select> dropdown with 4 categories: reports, resolutions, measures, and \
decisions. Each category has 5 items (20 total). Iterate all 4 filter values by \
changing the dropdown — each change navigates to ?category=value. Save each \
item to save_record with the item's link URL as core_id and include the \
category in the data.\
"""


def test_dropdown_filter_iteration(fixture_server):
    """Step 0 iterates 4 filter values, collects 20 records with category."""
    result = run_generation_pipeline("dropdown_filter", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 20)
    assert_fields_non_null(result, ["title", "category"])
