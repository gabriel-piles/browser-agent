"""Smoke + self-check report scoped to one subtask."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.processing_self_check_result import ProcessingSelfCheckResult
from browser_agent.domain.smoke_test_result import SmokeTestResult


class SubtaskSmokeReport(BaseModel):
    """Aggregated smoke-test and self-check outcomes for one subtask."""

    smoke: SmokeTestResult
    self_check: ProcessingSelfCheckResult | None = None
    discovery_self_check_failures: list[str] = Field(
        default_factory=list,
        description="Populated only for kind='discovery' via DiscoverySelfCheckVerifier",
    )
