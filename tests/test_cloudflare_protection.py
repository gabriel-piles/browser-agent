"""End-to-end test: Cloudflare-style JS challenge protection.

Scenario: The first request to the page returns a 503 with a JS
challenge that redirects after 2 seconds. The browser must execute
the JS to reach the real content (10 items). Tests that the agent's
browser can handle JS challenges and that wait_for_page_ready /
wait_for_anchors handle the redirect.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=cloudflare_protection and extract \
the title and date for every document item (10 items). The page may show a \
"Checking your browser" challenge before redirecting to the real content — \
wait for the page to fully load and the items to appear before extracting. \
Save each item to save_record with the item's link URL as source_url.\
"""


def test_cloudflare_protection(fixture_server):
    """Step 0 handles JS challenge: browser executes JS, reaches real content, extracts 10 items."""
    result = run_generation_pipeline("cloudflare_protection", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
