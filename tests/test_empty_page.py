"""End-to-end test: page with zero items.

Scenario: A valid page with a .item-list container but zero .item
elements. Tests that the agent handles zero items gracefully.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=empty_page and extract any \
document items on the page. The page may have zero items — if so, exit \
cleanly without crashing. Save any items found to save_record.\
"""


def test_empty_page(fixture_server):
    """Step 0 handles zero items: 0 records, driver exits 0."""
    result = run_generation_pipeline("empty_page", PROMPT, fixture_server)
    assert_driver_success(result)
    assert result["record_count"] == 0, f"Expected 0 records, got {result['record_count']}"
