"""End-to-end test: numbered pagination (page number links 1 2 3 4 5).

Scenario: 5 pages, 10 items each. Nav has numbered links ?page=1..5
plus Prev/Next. Tests that the agent handles numbered pagination.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=numbered_pagination and extract \
the title and date for every document item from the LISTING PAGES directly. \
There are 5 pages with 10 items each (50 total). Each item has an h3 a link \
and a span.date — extract both from the listing page. The page has numbered \
links (1 2 3 4 5) plus Prev/Next buttons. Use the a.next button to paginate \
through ALL pages — it links to ?page=N+1. The last page has no Next button. \
Save each item to save_record with the item's link URL as core_id. \
Use HTTP (not HTTPS) for all URLs.\
"""


def test_numbered_pagination(fixture_server):
    """Step 0 handles numbered pagination: 50 records."""
    result = run_generation_pipeline("numbered_pagination", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 50)
    assert_fields_non_null(result, ["title", "date"])
