"""Aggregated deterministic probe-corpus verification report.

Holds one :class:`ProbeResult` per probe and exposes a counts dict
mirroring :meth:`SyncPlan.total_counts`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.probe_result import ProbeResult, ProbeVerdict


class ProbeVerificationReport(BaseModel):
    """All probe results for one run plus a summary counts dict."""

    results: list[ProbeResult] = Field(default_factory=list, description="One ProbeResult per probe.")

    def counts(self) -> dict[str, int]:
        """Return ``{"total", "captured", "failed"}`` tally across results."""
        total = len(self.results)
        captured = sum(1 for r in self.results if r.verdict is ProbeVerdict.CAPTURED)
        return {"total": total, "captured": captured, "failed": total - captured}
