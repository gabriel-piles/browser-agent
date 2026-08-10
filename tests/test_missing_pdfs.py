"""End-to-end test: missing PDFs (404 downloads).

Scenario: 10 items, each linking to a PDF. PDFs 1-5 exist; PDFs
6-10 return 404. Tests that the agent:
1. Downloads the 5 available PDFs.
2. Handles the 5 missing PDFs gracefully — save_record with
   download_status="failed" rather than crashing.
3. Saves all 10 records to metadata.db.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=missing_pdfs and extract the title \
and date for every document item (10 items). Each item links to a PDF at /pdf/docN.pdf. \
Download every PDF — some PDFs may return 404 (not found). For missing PDFs, call \
save_record with download_status="failed" and pdf_filename="". For successful \
downloads, call save_record with download_status="downloaded" and the downloaded \
filename. Save ALL 10 items to save_record regardless of download success.\
"""


def test_missing_pdfs(fixture_server):
    """Step 0 handles 404 PDFs gracefully: 10 records, 5 PDFs downloaded, no crash."""
    result = run_generation_pipeline("missing_pdfs", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_pdf_count(result, 5)
    assert_fields_non_null(result, ["title", "date"])
