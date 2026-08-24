"""End-to-end test: worker pool must drain all discovered links.

Regression test for the global-gather-timeout bug where
``asyncio.wait_for(asyncio.gather(...), timeout=N)`` killed the worker
pool before all discovered links were processed, leaving the rest
``status='discovered'`` with no metadata rows. The linter (rules 15c
and 8a) and the system prompt now forbid this pattern and require an
unprocessed-link drain after the gather.

Reuses the concurrency fixture (50 items, detail pages). Runs the full
generation pipeline with parallel_runners=4, then verifies:
  1. The emitted processing script passes linter rules 15c + 8a.
  2. All 50 links were processed — zero rows left status='discovered'.
  3. All 50 metadata records were produced.
"""

from __future__ import annotations

from tests.conftest import (
    assert_all_links_processed,
    assert_driver_success,
    assert_fields_non_null,
    assert_linter_rules_clean,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=concurrency and extract the title \
and date for every document item (50 items). Each item links to a detail page. \
Save each item to save_record with the item's link URL as core_id. Use \
parallel processing to visit detail pages concurrently.\
"""


def test_unprocessed_links_drain(fixture_server, capsys):
    """Step 0 with parallel_runners=4: all 50 links drained, no leftovers."""
    with capsys.disabled():
        result = run_generation_pipeline("concurrency", PROMPT, fixture_server, parallel_runners=4)
    assert_driver_success(result)
    assert_linter_rules_clean(result, ["15c", "8a"])
    assert_min_records(result, 50)
    assert_all_links_processed(result)
    assert_fields_non_null(result, ["title", "date"])
