"""Result of running one subtask's emitted script as a subprocess."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScriptExecutionReport(BaseModel):
    """What happened when the subtask's script ran against the real site."""

    subtask_id: str
    script_path: str
    exit_code: int
    timed_out: bool
    duration_s: float
    output_tail: str = Field(
        default="",
        description="Last 100 lines of combined stdout+stderr",
    )
