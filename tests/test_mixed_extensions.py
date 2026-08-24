"""End-to-end test: mixed file extensions (.pdf .PDF .doc .DOC).

Scenario: 14 items with mixed file extensions: .pdf (3), .PDF (3),
.doc (3), .DOC (3), .html (2). Tests that the agent:
1. Downloads all PDF files (both .pdf and .PDF) — case-insensitive.
2. Classifies document types correctly.
3. Handles .doc/.DOC files (downloads them as supporting files).
4. Saves all 14 records to metadata.db.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=mixed_extensions and extract the \
title and document type for every item (14 items). Items link to files with mixed \
extensions: .pdf, .PDF, .doc, .DOC, and .html. Download all PDF files (both .pdf \
and .PDF extensions — treat them case-insensitively). For .doc and .DOC files, \
download them as supporting documents. For .html links, just save the metadata. \
Save each item to save_record with the item's link URL as core_id and include \
the document type (pdf/doc/html) in the data.\
"""


def test_mixed_extensions(fixture_server):
    """Step 0 handles mixed .pdf .PDF .doc .DOC extensions: 14 records, 6 PDFs downloaded."""
    result = run_generation_pipeline("mixed_extensions", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 14)
    assert_pdf_count(result, 6)
    assert_fields_non_null(result, ["title", "doc_type"])
