"""Concrete script_tools improvements derived from the verification agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScriptToolsFeedback(BaseModel):
    """Actionable script_tools changes the verifier recommends."""

    subtask_id: str
    improvements: list[str] = Field(
        description="Concrete, actionable script_tools changes",
    )
    summary: str = ""
