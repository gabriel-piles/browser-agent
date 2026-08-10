"""End-to-end test: PDF download needs session cookies.

Scenario: 5 items; PDF download only succeeds if the request includes
a session cookie set by the listing page. Tests cookie propagation.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=pdf_cookies and extract the \
title for every document item (5 items). The listing page sets a session cookie. \
PDF download only succeeds if the request includes this session cookie. \
Download every PDF. Save each item to save_record with \
download_status="downloaded".\
"""


def test_pdf_requires_cookies(fixture_server):
    """Step 0 handles cookie-protected PDFs: 5 records, 5 PDFs."""
    result = run_generation_pipeline("pdf_cookies", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    assert_pdf_count(result, 5)
