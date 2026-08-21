"""Input to the discovery-verification use case."""

from __future__ import annotations

from pydantic import BaseModel, Field


_TASK_DIRECTIVE = (
    "Verify that EVERY manifest target above was fully harvested — not a "
    "sample. For each target: navigate its URL with explore_page, scroll "
    "repeatedly to the bottom (and click any load-more control) until no "
    "new results appear, then count anchors matching the manifest's "
    "count_selector and compare that live count against BOTH the script's "
    "saved= figure and the DB inventory below. Confirm every zero-link "
    "target is genuinely empty on the live site (not a broken filter or a "
    "wrong URL). Check whether the same link appears under multiple "
    "targets — cross-target duplicates inflate totals; discovered_links "
    "deduplicates by URL. NEVER download anything. Return a "
    "VerificationReport with one missing_coverage entry per "
    "under-collected target, each with a concrete step_0_fix."
)


class DiscoveryVerificationRequest(BaseModel):
    """Input to the discovery-completeness verification use case.

    Carries the subtask description (source of truth), the full discovery
    script source, its parsed ``DISCOVERY_MANIFEST``, the script's own
    self-reported per-target stdout lines, and the deterministic DB
    inventory of what actually landed in ``discovered_links``.
    """

    task_prompt: str = Field(description="The subtask description from the scrape plan.")
    discovery_script: str = Field(description="Full source of the step 0 discovery script.")
    manifest_json: str = Field(description="Parsed DISCOVERY_MANIFEST pretty-printed as JSON.")
    target_report: str = Field(
        description="The script's own 'DISCOVERY target=… found=N saved=M' lines.",
    )
    db_inventory: str = Field(
        description="'label: db_count' lines from discovered_links GROUP BY filter_label.",
    )

    def render_prompt(self) -> str:
        parts = [
            f"## Original Task\n{self.task_prompt}\n\n---\n\n",
            f"## Discovery Script (from step 0)\n```python\n{self.discovery_script}\n```\n\n---\n\n",
            f"## Discovery Manifest\n```json\n{self.manifest_json}\n```\n\n---\n\n",
            f"## Script's Self-Reported Targets\n{self.target_report}\n\n---\n\n",
            f"## DB Inventory (discovered_links per filter_label)\n{self.db_inventory}\n\n---\n\n",
            _TASK_DIRECTIVE,
        ]
        return "".join(parts)
