"""End-to-end test: overly specific prompt with wrong CSS selectors.

Reuses the single_page_list fixture. Tests that the Explorer verifies
and corrects selectors during exploration, not trusting incorrect ones.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_page_list and extract the \
title and date for every document item. Use div.record h2 for the title and \
div.record span.dateval for the date. There are 10 items on a single page. \
Save each item to save_record with the item's link URL as source_url.\
"""


def test_overly_specific_prompt(fixture_server):
    """Step 0 corrects wrong selectors: 10 records despite bad CSS in prompt."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
