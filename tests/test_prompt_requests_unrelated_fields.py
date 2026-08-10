"""End-to-end test: prompt asks for fields not on the page.

Reuses the single_page_list fixture. Tests that the Explorer
identifies unavailable fields and the Processing Writer handles them.
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
title, date, author, and ISBN for every document item (10 items). The ISBN \
field may not exist on the page — set it to null if not found. Save each item \
to save_record with the item's link URL as source_url.\
"""


def test_prompt_requests_unrelated_fields(fixture_server):
    """Step 0 handles missing ISBN field: 10 records, title+date non-null."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
