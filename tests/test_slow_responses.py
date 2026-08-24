"""End-to-end test: slow server responses.

Scenario: A page with 10 items. The server adds a 3-second delay
to responses after the first 2 requests. Tests that the agent's
navigation and wait timeouts handle slow responses without
crashing or timing out prematurely.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=slow_responses and extract the \
title and date for every document item (10 items). The server may respond slowly \
to some requests — be patient and wait for the page to fully load before extracting. \
Save each item to save_record with the item's link URL as core_id.\
"""


def test_slow_responses(fixture_server):
    """Step 0 handles slow server responses: extracts 10 items despite 3s delays."""
    result = run_generation_pipeline("slow_responses", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
