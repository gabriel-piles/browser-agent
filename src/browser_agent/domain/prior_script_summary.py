"""One prior script found by the cross-run index — just enough for planner/builder context."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PriorScriptSummary(BaseModel):
    """Compact summary of one script from a prior run."""

    run_name: str = Field(description="Run directory name (e.g. 'HRC_resolutions_1_11')")
    script_path: str = Field(description="Absolute path to the .py file")
    kind: str = Field(description='"discovery" or "processing"')
    task_summary: str = Field(description="What the parent plan/task was about")
    subtask_description: str = Field(description="What this specific script does")
    verified_selectors: list[str] = Field(
        default_factory=list,
        description="CSS selectors the planner verified for this script",
    )
    pdf_download_strategy: str = Field(default="browser_fetch")
    status: str = Field(
        default="unknown",
        description='"succeeded", "verification_failed", "accepted_gap", or "unknown"',
    )
    site_overview: str = Field(default="")
