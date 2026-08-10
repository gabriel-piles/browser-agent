"""End-to-end test: large PDF file (5MB+).

Scenario: 3 items linking to large PDFs (~5MB each). Tests that the
agent downloads large PDFs without timeout.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=large_pdf and extract the title \
and date for every document item (3 items). Each item links to a large PDF \
(~5MB). Download every PDF. Save each item to save_record with \
download_status="downloaded" and the downloaded filename.\
"""


def test_large_pdf_download(fixture_server):
    """Step 0 downloads large PDFs: 3 records, 3 PDFs."""
    result = run_generation_pipeline("large_pdf", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_pdf_count(result, 3)
    assert_min_records(result, 3)
