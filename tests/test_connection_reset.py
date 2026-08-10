"""End-to-end test: connection reset mid-response.

Scenario: 10 items; every 3rd request gets a connection reset (RST).
Retry succeeds. Tests connection-reset resilience.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=connection_reset and extract the \
title and date for every document item (10 items). Some requests may get a \
connection reset — retry failed requests. Save each item to save_record with \
the item's link URL as source_url.\
"""


def test_connection_reset(fixture_server):
    """Step 0 handles connection resets: 10 records."""
    result = run_generation_pipeline("connection_reset", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
