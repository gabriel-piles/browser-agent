"""Input to the discovery-verification use case."""

from __future__ import annotations

from pydantic import BaseModel, Field


_TASK_DIRECTIVE = (
    "Verify the DISCOVERY subtask against ITS OWN declared scope — the subtask "
    "description below plus its DISCOVERY_MANIFEST — not against the full "
    "end-to-end download task. A discovery subtask's job is to enumerate the "
    "manifest's targets and persist each result link under ONE of the downstream "
    "filter_labels. It must NOT be expected to also extract individual documents "
    "unless the subtask description explicitly says so.\n\n"
    "Audit, in this order:\n"
    "1. Confirm the manifest's listing URL / link_selector / index_from_href / "
    "index_range / target_url_transform are consistent with the live listing "
    "page (navigate the listing once).\n"
    "2. For every downstream label whose DB inventory count is 0 or clearly "
    "below the manifest's min_per_target, navigate the corresponding live "
    "target(s) and decide whether it is genuinely empty or a broken selector / "
    "skipped target / early pagination stop.\n"
    "3. Confirm each persisted label is one of the downstream labels (a label "
    "outside that set is silent data loss — flag it even though the script "
    "'succeeded').\n"
    "4. Confirm links are saved once (cross-target duplicates are deduplicated "
    "by URL and deflate totals).\n\n"
    "Re-walk ONLY the zero/under-collected labels — do not re-enumerate every "
    "well-populated target. NEVER download anything. Return a "
    "VerificationReport with one missing_coverage entry per under-collected / "
    "mis-targeted label and a concrete step_0_fix."
)


class DiscoveryVerificationRequest(BaseModel):
    """Input to the discovery-completeness verification use case.

    Carries the subtask description (the scoped source of truth), the full
    discovery script source, its parsed ``DISCOVERY_MANIFEST``, the script's
    own self-reported per-target stdout lines, the deterministic DB inventory,
    and the downstream labels the processing subtasks will consume.
    """

    task_prompt: str = Field(description="The subtask description from the scrape plan (scoped source of truth).")
    discovery_script: str = Field(description="Full source of the step 0 discovery script.")
    manifest_json: str = Field(description="Parsed DISCOVERY_MANIFEST pretty-printed as JSON.")
    target_report: str = Field(
        description="The script's own 'DISCOVERY target=… found=N saved=M' lines.",
    )
    db_inventory: str = Field(
        description="'label: db_count' lines from discovered_links GROUP BY filter_label.",
    )
    downstream_labels: list[str] = Field(
        default_factory=list,
        description="Exact filter_labels the downstream processing subtasks will consume.",
    )

    def render_prompt(self) -> str:
        parts = [
            f"## Discovery Subtask (source of truth — do not expand its scope)\n{self.task_prompt}\n\n---\n\n",
            f"## Downstream filter_labels (every saved link MUST carry one of these)\n{self.downstream_labels or '[]'}\n\n---\n\n",
            f"## Discovery Script (from step 0)\n```python\n{self.discovery_script}\n```\n\n---\n\n",
            f"## Discovery Manifest\n```json\n{self.manifest_json}\n```\n\n---\n\n",
            f"## Script's Self-Reported Targets\n{self.target_report}\n\n---\n\n",
            f"## DB Inventory (discovered_links per filter_label)\n{self.db_inventory}\n\n---\n\n",
            _TASK_DIRECTIVE,
        ]
        return "".join(parts)
