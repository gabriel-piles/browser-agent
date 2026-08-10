"""End-to-end test: DNS failure on broken sub-resource.

Scenario: 10 items; the page references a broken sub-resource
(broken image/iframe from nonexistent.invalid). Content is still
extractable. Tests that wait_for_page_ready doesn't hang.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=dns_failure and extract the \
title and date for every document item (10 items). The page has broken \
sub-resources (images/iframes from a non-existent domain) — ignore them and \
extract the content. Save each item to save_record with the item's link URL \
as source_url.\
"""


def test_dns_failure_retry(fixture_server):
    """Step 0 ignores broken sub-resources: 10 records."""
    result = run_generation_pipeline("dns_failure", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
