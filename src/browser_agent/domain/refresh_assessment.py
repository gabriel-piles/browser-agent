"""Deterministic gap and new-content evidence for a refresh pass."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.failed_document import FailedDocument
from browser_agent.domain.new_discovered_link import NewDiscoveredLink


class RefreshAssessment(BaseModel):
    """What a refresh pass found: retryable failures plus new links."""

    failed_documents: list[FailedDocument] = Field(default_factory=list)
    new_discovered_links: list[NewDiscoveredLink] = Field(default_factory=list)
