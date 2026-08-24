"""End-to-end test: prior report feedback is used.

Reuses the single_page_list fixture. Tests PriorReportReader plumbing
doesn't crash when a prior report exists.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)
from browser_agent.configuration import RUNS_PATH

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_page_list and extract the \
title and date for every document item (10 items). Save each item to \
save_record with the item's link URL as core_id.\
"""

_PRIOR_REPORT = """# Prior Run Report

## Issues Found
- The previous script missed 2 items due to incorrect CSS selector.
- Use div.item (not div.record) for item containers.

## Recommendations
- Verify the h3 a selector for titles.
- Ensure span.date is used for dates.
"""


def test_prior_feedback_integration(fixture_server):
    """Step 0 integrates prior report feedback: 10 records, no crash."""
    run_name = "e2e_single_page_list"
    run_path = RUNS_PATH / run_name
    reports_dir = run_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "report.md").write_text(_PRIOR_REPORT, encoding="utf-8")
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
