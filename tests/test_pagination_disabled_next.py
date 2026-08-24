"""End-to-end test: Next button with disabled class.

Scenario: 5 pages; the Next button is present on the last page but
has class="next disabled" and no href. Tests that the agent detects
the disabled state and stops.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=disabled_next and extract the \
title and date for every document item across ALL pages. There are 5 pages with \
10 items each (50 total). Use the a.next button to paginate. On the LAST page, \
the Next button is present but has class="next disabled" and NO href — do NOT \
click it, stop pagination there. Save each item to save_record with the item's \
link URL as core_id.\
"""


def test_pagination_disabled_next(fixture_server):
    """Step 0 detects disabled Next button: stops, 50 records."""
    result = run_generation_pipeline("disabled_next", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
