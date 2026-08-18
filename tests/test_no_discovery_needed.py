"""End-to-end test: needs_discovery=false (single page, no pagination).

Scenario: 10 items on one page, each linking directly to a PDF, no
pagination, no filters, no scroll. Tests that the Explorer correctly sets
needs_discovery=false; the Discovery Writer is skipped; the Processing
Writer downloads every PDF on the single listing page. No discovery
script is emitted.
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
Navigate to http://127.0.0.1:{PORT}/?scenario=no_discovery and extract the title \
and date for every document item on the page (10 items). Each item is in a \
div.item with an h3 a link to a PDF and a span.date. Download every PDF (10 PDFs). \
Save each item to save_record with the item's link URL as source_url, \
download_status="downloaded", and the downloaded filename. This is a single \
page — do NOT paginate, scroll, or click anything.\
"""


def test_no_discovery_needed(fixture_server, capsys):
    """Step 0 with needs_discovery=false: 10 records, 10 PDFs, no discovery script."""
    with capsys.disabled():
        result = run_generation_pipeline("no_discovery", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_pdf_count(result, 10)
    assert_fields_non_null(result, ["title", "date"])
    # Verify no discovery script was emitted
    scripts_dir = result["run_path"] / "scripts"
    if scripts_dir.is_dir():
        discovery_scripts = [p for p in scripts_dir.glob("*.py") if "__discover__" in p.name]
        assert len(discovery_scripts) == 0, f"Expected no discovery script, found {discovery_scripts}"
