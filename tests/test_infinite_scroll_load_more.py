"""End-to-end test: AJAX load-more button.

Scenario: 10 items initially, a "Load more" button fetches
/fragment/?page=N via fetch() and appends HTML. After 3 pages
(30 items) the button disappears.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=infinite_scroll and extract the \
title and date for every document item. There are 30 items total across 3 pages. \
Click the "Load more" button to load additional items — the button fetches more \
items via AJAX and appends them to the list. Keep clicking until the button \
disappears (all 30 items loaded). Save each item to save_record with the item's \
link URL as source_url.\
"""


def test_infinite_scroll_load_more(fixture_server):
    """Step 0 handles AJAX load-more: clicks button until gone, 30 items."""
    result = run_generation_pipeline("infinite_scroll", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])


# Note: Discovery Writer must handle AJAX-loaded content + button-click loop
# + termination condition (button removal). The discovery self-check and
# audit verify all items were collected.
