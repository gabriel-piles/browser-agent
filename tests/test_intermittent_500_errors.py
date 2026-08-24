"""End-to-end test: intermittent 500 errors.

Scenario: 5 pages; pages 2 and 4 return HTTP 500 on the first
request, succeed on retry. 50 items total. Tests retry logic.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=intermittent_500 and extract the \
title and date for every document item across ALL pages (50 items, 5 pages). \
Some pages may return HTTP 500 on the first request — retry failed pages. Use \
the a.next button to paginate. Save each item to save_record with the item's \
link URL as core_id.\
"""


def test_intermittent_500_errors(fixture_server):
    """Step 0 retries 500 errors: 50 records."""
    result = run_generation_pipeline("intermittent_500", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
