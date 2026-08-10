"""End-to-end test: page with exactly 1 item.

Scenario: 1 item on a single page. Tests boundary condition
and pagination loop termination.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_item and extract the title \
and date for the document item on the page (1 item). Save it to save_record \
with the item's link URL as source_url. Do NOT paginate.\
"""


def test_single_item(fixture_server):
    """Step 0 handles single item: 1 record."""
    result = run_generation_pipeline("single_item", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 1)
    assert_fields_non_null(result, ["title", "date"])
