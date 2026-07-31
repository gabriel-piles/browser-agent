"""Verdict of the link-discovery verification stage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LinkDiscoveryVerdict(BaseModel):
    """Outcome of executing the discovery-verification script."""

    status: Literal["passed", "under_collected", "inconclusive", "unavailable"]
    report: str = ""
    under_collected_paths: list[str] = Field(default_factory=list)
