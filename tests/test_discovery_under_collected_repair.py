"""End-to-end test: discovery under-collected repair.

Scenario: 4 filter values, 5 items each (20 total). The 4th filter
value is behind a "More filters" expand button. Tests the discovery
self-check UNDER-COLLECTED repair.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=discovery_undercollect and \
extract the title and category for every document item across ALL filter \
values. There is a <select> dropdown with categories, but one category is \
hidden behind a "More filters" button — click the button to reveal all \
options. There are 4 categories with 5 items each (20 total). Iterate ALL \
filter values. Save each item to save_record with the item's link URL as \
source_url and include the category.\
"""


def test_discovery_under_collected_repair(fixture_server):
    """Step 0 self-check repairs UNDER-COLLECTED: 20 records."""
    result = run_generation_pipeline("discovery_undercollect", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 20)
    assert_fields_non_null(result, ["title", "category"])
