"""End-to-end test: PDF download modal.

Scenario: 5 items, each with a download button that opens a modal
with a PDF link. Tests that the agent downloads all 5 PDFs.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=pdf_download_modal and extract the \
title for every document item (5 items). Each item has a "Download" button that \
opens a modal with a PDF link. Download every PDF — the PDF link is in the modal \
that appears when you click the Download button. Save each item to save_record \
with download_status="downloaded" and the downloaded filename.\
"""


def test_pdf_download_modal(fixture_server, capsys):
    """Step 0 handles modal PDF download: 5 records, 5 PDFs."""
    with capsys.disabled():
        result = run_generation_pipeline("pdf_download_modal", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    assert_pdf_count(result, 5)
    assert_fields_non_null(result, ["title"])
