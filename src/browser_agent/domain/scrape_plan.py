"""A plan that divides one scraping task into one script per subtask."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.subtask_spec import SubtaskSpec


class ScrapePlan(BaseModel):
    """Self-contained plan: task_summary, site_overview, and ordered subtasks."""

    task_summary: str = Field(
        description="One-sentence summary of what the operator asked for",
    )
    site_overview: str = Field(
        description="Human-readable summary of the site structure for logging",
    )
    subtasks: list[SubtaskSpec] = Field(
        description="Ordered list of subtasks — exactly one script per subtask",
    )
    planner_notes: str = Field(
        default="",
        description="Any notes from the planner (caveats, assumptions, split rationales)",
    )
