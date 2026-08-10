"""End-to-end test: list page → detail page extraction.

Scenario: A listing page with 10 items, each linking to a detail page
/doc/N with full metadata (title, date, author, description, PDF link).
Tests that the agent navigates to each detail page, extracts fields,
and downloads the PDF.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=detail_page and extract metadata \
for every document (10 items). The listing page has 10 items, each linking to a \
detail page at /doc/N. Navigate to EACH detail page and extract the title, date, \
author, and description. Each detail page also has a PDF link — download every \
PDF (5 PDFs available). Save each item to save_record with the detail page URL \
as source_url and include all extracted fields in the data.\
"""


def test_detail_page_extraction(fixture_server):
    """Step 0 navigates detail pages: 10 records, 5 PDFs, multi-field metadata."""
    result = run_generation_pipeline("detail_page", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_pdf_count(result, 5)
    assert_fields_non_null(result, ["title", "date", "author", "description"])
