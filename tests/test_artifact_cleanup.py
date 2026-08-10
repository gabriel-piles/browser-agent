"""End-to-end test: intermediate files removed after generation.

Reuses the single_page_list fixture. Tests _cleanup_emit_artifacts.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_page_list and extract the \
title and date for every document item (10 items). Each item is in a div.item \
with an h3 a link and a span.date. Save each item to save_record with the \
item's link URL as source_url.\
"""


def test_artifact_cleanup(fixture_server):
    """Step 0 cleans intermediate artifacts: no .raw.py or .json sidecars."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
    scripts_dir = result["run_path"] / "scripts"
    if not scripts_dir.is_dir():
        return
    raw_files = list(scripts_dir.glob("*.raw.py"))
    json_files = list(scripts_dir.glob("*.json"))
    py_files = list(scripts_dir.glob("*.py"))
    assert len(raw_files) == 0, f"Found .raw.py files: {raw_files}"
    assert len(json_files) == 0, f"Found .json sidecar files: {json_files}"
    # At most 1 processing .py and 1 discovery .py
    processing_py = [p for p in py_files if "discover" not in p.name]
    assert len(processing_py) <= 1, f"Found {len(processing_py)} processing scripts, expected <=1"
