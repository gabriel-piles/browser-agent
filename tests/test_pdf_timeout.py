"""End-to-end test: PDF download times out (server hangs).

Scenario: 5 items; PDFs 1-3 download normally, PDFs 4-5 cause the
server to hang. Tests that the agent handles timeouts gracefully.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=pdf_timeout and extract the \
title for every document item (5 items). Each item links to a PDF. PDFs 1-3 \
download normally. PDFs 4-5 cause the server to hang (never respond). Handle the \
timeouts gracefully — call save_record with download_status="failed" or \
"timeout" for those. Save ALL 5 items to save_record.\
"""


def test_pdf_timeout(fixture_server):
    """Step 0 handles PDF timeout: 5 records, 3 PDFs downloaded."""
    result = run_generation_pipeline("pdf_timeout", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_pdf_count(result, 3)
    assert_min_records(result, 5)
