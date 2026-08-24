"""End-to-end test: parallel_runners=4.

Scenario: 50 items, each linking to a detail page. Designed for
parallel_runners=4. Tests that the agent generates a script compatible
with parallel processing.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=concurrency and extract the title \
and date for every document item (50 items). Each item links to a detail page. \
Save each item to save_record with the item's link URL as core_id. Use \
parallel processing to visit detail pages concurrently.\
"""


def test_concurrency_parallel_runners(fixture_server):
    """Step 0 with parallel_runners=4: 50 records, no crash."""
    result = run_generation_pipeline("concurrency", PROMPT, fixture_server, parallel_runners=4)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
