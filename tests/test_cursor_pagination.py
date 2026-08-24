"""End-to-end test: cursor/offset-based pagination.

Scenario: Pages use ?after=<last_id> (cursor-based). 3 pages, 10
items each. No Next/Prev buttons — the script must construct the
next URL from the last item's ID.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=cursor_pagination and extract the \
title and date for every document item across ALL pages (30 items). This site \
uses cursor-based pagination: the URL has ?after=<last_id>. The page shows 10 \
items and a link with the next cursor (a.next-cursor). Follow the cursor links \
to paginate through all 3 pages. Save each item to save_record with the item's \
link URL as core_id.\
"""


def test_cursor_pagination(fixture_server):
    """Step 0 handles cursor pagination: 30 records."""
    result = run_generation_pipeline("cursor_pagination", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 30)
    assert_fields_non_null(result, ["title", "date"])
