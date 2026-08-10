"""End-to-end test: prompt explicitly says download all PDFs.

Reuses the mixed_content fixture (5 PDFs). Tests PDF strategy probing.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_min_records,
    assert_pdf_count,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=mixed_content and extract all \
items and download every PDF you find. Save each item to save_record.\
"""


def test_prompt_with_pdf_instruction(fixture_server):
    """Step 0 with PDF instruction: 5 records, 5 PDFs."""
    result = run_generation_pipeline("mixed_content", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_pdf_count(result, 5)
    assert_min_records(result, 5)
