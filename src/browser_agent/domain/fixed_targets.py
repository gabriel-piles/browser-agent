"""Fixed list of discovery target URLs (IACHR-style category list)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from browser_agent.domain.discovery_target import DiscoveryTarget


class FixedTargets(BaseModel):
    """A fixed, explicit list of target pages to walk."""

    kind: Literal["fixed"] = "fixed"
    items: list[DiscoveryTarget]
