"""End-to-end test: DiscoveryAuditor finds missed filter.

Scenario: 3 categories, 5 items each (15 total). The listing page has
a hidden data-count attribute on each <option> for the auditor to
use as an oracle. Tests the DiscoveryAuditor cross-check repair.
"""

from __future__ import annotations

from tests.conftest import (
    assert_driver_success,
    assert_fields_non_null,
    assert_min_records,
    run_generation_pipeline,
)

PROMPT = """\
Navigate to http://127.0.0.1:{PORT}/?scenario=audit_discrepancy and extract \
the title and category for every document item across ALL filter values. \
There are 3 categories in a <select> dropdown: reports, resolutions, measures. \
Each category has 5 items (15 total). Each <option> has a data-count attribute \
showing the expected item count. Iterate ALL 3 filter values. Save each item \
to save_record with the item's link URL as source_url and include the category.\
"""


def test_discovery_audit_discrepancy(fixture_server):
    """Step 0 audit repairs discrepancy: 15 records."""
    result = run_generation_pipeline("audit_discrepancy", PROMPT, fixture_server)
    assert_driver_success(result)
    assert_min_records(result, 15)
    assert_fields_non_null(result, ["title", "category"])
