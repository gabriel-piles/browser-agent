"""Per-split state persisted as ``state.json`` inside the split folder."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.flow_script_record import FlowScriptRecord


class SplitRunState(BaseModel):
    """Resume-safe state for one split folder's run.

    Written after every phase so an interrupted ``step_1_run_prompts``
    invocation resumes where it stopped: terminal scripts are skipped,
    the current phase re-runs.
    """

    split_name: str = Field(description="Folder name under ``flow/`` this state belongs to.")
    attempts: int = Field(default=0, ge=0, description="Build attempts used across the split's primary script.")
    spec: dict[str, str | int | float | bool | list | None] = Field(
        default_factory=dict,
        description="The explored FlowSubtaskSpec as JSON (empty before exploration).",
    )
    scripts: list[FlowScriptRecord] = Field(
        default_factory=list,
        description="One record per emitted script, index order (0 = primary).",
    )
    finished: bool = Field(default=False, description="True when the split reached a terminal outcome.")
    status: str = Field(
        default="pending",
        description="Split-level outcome: pending, building, lint_failed, smoke_failed, execution_failed, verification_failed, succeeded, accepted_gap.",
    )
    started_at: str = Field(default="", description="ISO timestamp of the first build attempt (empty before).")
    finished_at: str = Field(default="", description="ISO timestamp of the terminal outcome (empty while running).")
