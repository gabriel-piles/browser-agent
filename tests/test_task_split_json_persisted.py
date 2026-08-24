"""End-to-end test: task_split.json is written.

Reuses the single_page_list fixture. Tests _persist_split.
"""

from __future__ import annotations

import json

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=single_page_list and extract the \
title and date for every document item (10 items). Save each item to \
save_record with the item's link URL as core_id.\
"""


def test_task_split_json_persisted(fixture_server):
    """Step 0 writes task_split.json with required fields."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
    split_path = result["run_path"] / "task_split.json"
    assert split_path.exists(), f"task_split.json not found at {split_path}"
    data = json.loads(split_path.read_text(encoding="utf-8"))
    for field in ("needs_discovery", "discovery_prompt", "processing_prompt", "sample_document_urls"):
        assert field in data, f"Missing field '{field}' in task_split.json"
