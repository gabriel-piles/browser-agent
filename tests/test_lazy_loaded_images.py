"""End-to-end test: images with loading="lazy".

Scenario: 10 items; each has an <img> with loading="lazy" and
data-src. Images only load when scrolled into view. Tests that
the agent doesn't depend on img.src being populated.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=lazy_images and extract the title \
and date for every document item (10 items). Each item has a lazy-loaded image \
— the img src may be empty until scrolled into view. Do NOT depend on img.src. \
Save each item to save_record with the item's link URL as source_url.\
"""


def test_lazy_loaded_images(fixture_server):
    """Step 0 handles lazy images: 10 records."""
    result = run_generation_pipeline("lazy_images", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 10)
    assert_fields_non_null(result, ["title", "date"])
