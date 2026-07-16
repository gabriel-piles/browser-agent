"""Push the plan to Uwazi, or return a synthetic dry-run :class:`ApplyResult`.

Hides the ``push_plan`` call + the dry-run construction
behind one object. When ``push`` is False the executor returns
an :class:`ApplyResult` aggregated from the plan's per-row
``action`` values so the rest of the driver can print the same
shape of report whether the work was actually sent to Uwazi
or just simulated locally.
"""

from __future__ import annotations

from browser_agent.domain.apply_result import ApplyResult
from browser_agent.use_cases.apply_mapping_use_case import push_plan
from uwazi_api.client import UwaziClient


class ApplyPlanExecutor:
    """Execute a :class:`SyncPlan`, either by pushing to Uwazi or by dry-running."""

    def __init__(self, client: UwaziClient) -> None:
        self._client = client

    def execute(self, plan, push: bool):
        """Push ``plan`` to Uwazi when ``push`` is True, else return a dry-run result."""
        if push:
            return push_plan(plan=plan, client=self._client)
        print("push=False; skipping the actual Uwazi calls and printing the plan only.")
        return self._dry_run_result(plan)

    def _dry_run_result(self, plan) -> ApplyResult:
        """Build a synthetic :class:`ApplyResult` aggregated from ``plan`` rows."""
        out = ApplyResult()
        for row in plan.rows:
            bucket = out.per_language_counts.setdefault(row.language, {})
            bucket[row.action.value] = bucket.get(row.action.value, 0) + 1
        return out
