"""End-to-end test: parallel_runners=1 (sequential baseline).

Reuses the concurrency fixture (50 items). Tests the sequential
processing baseline.
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
and date for every document item (50 items). Save each item to save_record \
with the item's link URL as core_id.\
"""


def test_parallel_runners_1(fixture_server):
    """Step 0 with parallel_runners=1: 50 records, sequential baseline."""
    result = run_generation_pipeline("concurrency", PROMPT, fixture_server, parallel_runners=1)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
