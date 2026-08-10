"""End-to-end test: vague prompt with no CSS selectors.

Reuses the single_page_list fixture. Tests autonomous selector
discovery by the Explorer agent.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Go to http://127.0.0.1:{PORT}/?scenario=single_page_list and get all the \
document titles and dates. Save each one.\
"""


def test_vague_prompt(fixture_server):
    """Step 0 with vague prompt: Explorer discovers selectors, 10 records."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
