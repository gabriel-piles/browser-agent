"""End-to-end test: lint + smoke repair loop regression.

Reuses the single_page_list fixture. This is a regression test that
the repair loops exist and don't crash even when not triggered.
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
title, date, and author for every document item. Each item is in a div.item \
with an h3 a link, a span.date, and a span.author. Save each item to save_record \
with the item's link URL as source_url. There are 10 items.\
"""


def test_smoke_test_catches_syntax_error(fixture_server):
    """Step 0 repair loops don't crash: 10 records via single_page_list."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
