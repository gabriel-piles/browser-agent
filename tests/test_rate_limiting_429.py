"""End-to-end test: rate limiting 429 with Retry-After.

Scenario: 3 pages, 10 items each. After every 2 requests, server
returns 429 with Retry-After: 1. Retry after 1s succeeds. 30 records.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=rate_limit_429 and extract the \
title and date for every document item across ALL pages (30 items, 3 pages). \
The server may return HTTP 429 with a Retry-After header — wait and retry. Use \
the a.next button to paginate. Save each item to save_record with the item's \
link URL as core_id.\
"""


def test_rate_limiting_429(fixture_server):
    """Step 0 respects 429 + Retry-After: 30 records."""
    result = run_generation_pipeline("rate_limit_429", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
