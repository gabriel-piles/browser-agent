"""End-to-end test: SPA with delayed JS rendering.

Scenario: 10 items rendered after a 1500ms setTimeout; pure static
+ JS (no _dynamic.py). Tests that the agent handles client-side
rendering and waits for the setTimeout to populate the DOM.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=spa_dynamic and extract the title \
and date for every document item (10 items). The items are rendered dynamically \
via JavaScript after a 1500ms delay — wait for the page to fully load and the \
items to appear in the DOM before extracting. Save each item to save_record with \
the item's link URL as source_url.\
"""


def test_spa_dynamic_rendering(fixture_server):
    """Step 0 handles JS-rendered SPA: waits for setTimeout, extracts 10 items."""
    result = run_generation_pipeline("spa_dynamic", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
