"""End-to-end test: links with target="_blank".

Scenario: 10 items; each link has target="_blank". Clicking opens
a new tab. Tests that the agent handles target="_blank" links.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=new_tab and extract the title \
and date for every document item (10 items). Each link has target="_blank" — \
handle this by using the href directly or removing the target attribute before \
clicking. Save each item to save_record with the item's link URL as core_id.\
"""


def test_new_tab_links(fixture_server):
    """Step 0 handles target=_blank: 10 records."""
    result = run_generation_pipeline("new_tab", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
