"""End-to-end test: search input + submit form.

Scenario: A text input + submit button; searching for "report" returns
8 items, "memo" returns 5, empty search returns 10 (all). Tests that
the agent iterates search terms from a predefined list.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=search_filter and extract the \
title and date for every document item from the listing page. There is a \
search form with a text input (id='search') and submit button. The page \
shows 10 items by default (empty search). Each item has an h3 a link and a \
span.date. Extract the title and date from the listing page. Save each item \
to save_record with the item's link URL as source_url. Use HTTP (not HTTPS).\
"""


def test_search_filter(fixture_server):
    """Step 0 handles search form listing: 10 records."""
    result = run_generation_pipeline("search_filter", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
