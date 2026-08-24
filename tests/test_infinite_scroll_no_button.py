"""End-to-end test: scroll-to-load (no button).

Scenario: 30 items total; 10 visible, more loaded by scrolling to
the bottom (IntersectionObserver). No "Load more" button.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=scroll_load_no_button and extract \
the title and date for every document item (30 items total). Items are loaded \
dynamically by scrolling to the bottom of the page — there is NO "Load more" \
button. Scroll down repeatedly until no new items appear. Save each item to \
save_record with the item's link URL as core_id.\
"""


def test_infinite_scroll_no_button(fixture_server):
    """Step 0 handles scroll-to-load: scrolls until no new items, 30 records."""
    result = run_generation_pipeline("scroll_load_no_button", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
