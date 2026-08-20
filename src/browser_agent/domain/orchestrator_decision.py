"""One LLM judgment point in the orchestrator's control loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OrchestratorDecision(BaseModel):
    """The orchestrator's single-turn response to a failure or plan review."""

    action: Literal[
        "accept_plan",
        "replan",
        "repair",
        "accept_gap",
        "abort",
        "finish",
    ] = Field(description="What to do next")
    subtask_id: str = ""
    focus: str = Field(
        default="",
        description="Repair/replan instruction for the next agent turn",
    )
    reasoning: str = Field(
        description="Why this action was chosen over alternatives",
    )
