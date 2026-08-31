"""The discover agent's output: a WHAT-scoped split of the whole task."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.task_split import TaskSplit


class DiscoverPlan(BaseModel):
    """Whole-task split produced by the step-0 discover agent."""

    task_summary: str = Field(
        description="One-sentence summary of what the operator asked for",
    )
    site_overview: str = Field(
        description="Human-readable summary of the site/document structure",
    )
    splits: list[TaskSplit] = Field(
        default_factory=list,
        description="Empty list means an incremental pass found nothing new",
    )
    discoverer_notes: str = Field(
        default="",
        description="Any notes from the discoverer (caveats, boundary corrections)",
    )
