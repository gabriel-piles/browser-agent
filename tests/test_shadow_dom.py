"""End-to-end test: items inside a Shadow DOM.

Scenario: 10 items rendered inside a custom element with a Shadow
DOM (attachShadow). Items are not accessible via standard CSS
selectors.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=shadow_dom and extract the title \
and date for every document item (10 items). The items are rendered inside a \
Shadow DOM — use shadow-piercing selectors or shadowRoot.querySelector to \
access them. Save each item to save_record with the item's link URL as \
source_url.\
"""


def test_shadow_dom(fixture_server):
    """Step 0 pierces Shadow DOM: 10 records."""
    result = run_generation_pipeline("shadow_dom", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
