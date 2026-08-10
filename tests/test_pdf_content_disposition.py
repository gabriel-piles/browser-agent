"""End-to-end test: PDF served with Content-Disposition: attachment.

Scenario: 5 items; server sends Content-Disposition header with a
custom filename. Tests that the agent downloads 5 PDFs regardless
of filename mismatch.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=pdf_content_disposition and \
extract the title for every document item (5 items). Each item links to a PDF. \
The server sends a Content-Disposition: attachment header with a custom filename. \
Download every PDF — the downloaded filename may differ from the URL basename. \
Save each item to save_record with download_status="downloaded".\
"""


def test_pdf_content_disposition(fixture_server):
    """Step 0 handles Content-Disposition: 5 records, 5 PDFs."""
    result = run_generation_pipeline("pdf_content_disposition", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    assert_pdf_count(result, 5)
