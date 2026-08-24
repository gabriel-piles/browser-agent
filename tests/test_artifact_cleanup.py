"""End-to-end test: emitted script artifacts.

Reuses the single_page_list fixture. The emitter intentionally persists
a ``.raw.py`` and a ``.json`` sidecar next to every emitted script;
assert those sidecars exist and no duplicate processing script remains.
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
item's link URL as core_id.\
"""


def test_artifact_cleanup(fixture_server):
    """Each emitted script keeps .raw.py/.json sidecars; no duplicates."""
    result = run_generation_pipeline("single_page_list", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
    scripts_dir = result["run_path"] / "scripts"
    if not scripts_dir.is_dir():
        return
    py_files = list(scripts_dir.glob("*.py"))
    processing_py = [p for p in py_files if "discover" not in p.name and not p.name.endswith(".raw.py")]
    assert len(processing_py) <= 1, f"Found {len(processing_py)} processing scripts, expected <=1"
    for path in processing_py:
        assert path.with_suffix(".raw.py").exists(), f"Missing raw sidecar for {path}"
        assert path.with_suffix(".json").exists(), f"Missing json sidecar for {path}"
