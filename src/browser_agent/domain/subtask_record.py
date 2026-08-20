"""Per-subtask lifecycle record tracked by the orchestrator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SubtaskStatus = Literal[
    "pending",
    "building",
    "smoke_failed",
    "execution_failed",
    "verification_failed",
    "repair_noop",
    "succeeded",
    "accepted_gap",
    "aborted",
]


class SubtaskRecord(BaseModel):
    """The orchestrator's per-subtask tracking row — built over the pipeline."""

    subtask_id: str
    status: SubtaskStatus = "pending"
    attempts: int = 0
    repair_decisions: int = Field(
        default=0,
        description="Orchestrator-level repair decisions (hard-capped at MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK)",
    )
    script_path: str = ""
    script_hash: str = Field(
        default="",
        description="md5 of the emitted .py — detects repair stagnation",
    )
    plan_index: int = Field(
        default=1,
        description="Which plan_NNN.json this subtask was part of",
    )
