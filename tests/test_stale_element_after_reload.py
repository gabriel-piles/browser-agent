"""End-to-end test: page reloads during extraction (stale element).

Scenario: 10 items; after the page loads, a client-side script
re-renders the item list after 2s. Tests that the agent waits for
the re-render to settle.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=stale_element and extract the \
title and date for every document item (10 items). The page re-renders its \
item list 2 seconds after loading — wait for the DOM to settle before \
extracting. Save each item to save_record with the item's link URL as \
source_url.\
"""


def test_stale_element_after_reload(fixture_server):
    """Step 0 handles DOM mutation: waits for re-render, 10 records."""
    result = run_generation_pipeline("stale_element", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
