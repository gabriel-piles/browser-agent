"""One collection target URL the discovery script enumerates."""

from __future__ import annotations

from pydantic import BaseModel


class DiscoveryTarget(BaseModel):
    """A single target page with a human-readable label and its URL."""

    label: str
    url: str
