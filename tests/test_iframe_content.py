"""End-to-end test: items inside an <iframe>.

Scenario: 10 items rendered inside an <iframe src="/inner.html">.
The iframe content must be accessed via frame.contentDocument.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=iframe_content and extract the \
title and date for every document item (10 items). The items are rendered \
inside an <iframe> — you must switch to the iframe context to access them. \
Save each item to save_record with the item's link URL as core_id.\
"""


def test_iframe_content(fixture_server):
    """Step 0 pierces iframe: 10 records."""
    result = run_generation_pipeline("iframe_content", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
