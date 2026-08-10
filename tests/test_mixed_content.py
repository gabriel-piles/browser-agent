"""End-to-end test: mixed PDF + HTML content.

Scenario: 10 items — 5 link to PDFs, 5 link to HTML pages. Tests
that the agent distinguishes PDF links from HTML links and downloads
only PDFs.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=mixed_content and extract the \
title for every document item (10 items). Some items link to PDFs and some to \
HTML pages. Download all PDF files (5 PDFs). For HTML links, just save the \
metadata. Save each item to save_record with the item's link URL as source_url.\
"""


def test_mixed_content(fixture_server):
    """Step 0 distinguishes PDF vs HTML: 5 records, 5 PDFs downloaded."""
    result = run_generation_pipeline("mixed_content", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 5)
    assert_pdf_count(result, 5)
