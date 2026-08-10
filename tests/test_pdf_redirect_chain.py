"""End-to-end test: PDF URL redirects (301/302).

Scenario: 5 items; /pdf/docN.pdf redirects (301) to /file/docN.pdf.
Tests that the agent follows redirects and downloads all 5 PDFs.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=pdf_redirect and extract the \
title for every document item (5 items). Each item links to a PDF at \
/pdf/docN.pdf which redirects (301) to /file/docN.pdf. Follow the redirects and \
download every PDF. Save each item to save_record with \
download_status="downloaded".\
"""


def test_pdf_redirect_chain(fixture_server):
    """Step 0 follows PDF redirects: 5 records, 5 PDFs."""
    result = run_generation_pipeline("pdf_redirect", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    assert_pdf_count(result, 5)
