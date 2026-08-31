"""One emitted script within one split folder — the flow's tracking row."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ScriptStatus = Literal[
    "pending",
    "building",
    "lint_failed",
    "smoke_failed",
    "execution_failed",
    "verification_failed",
    "repair_noop",
    "emit_budget_exhausted",
    "succeeded",
    "accepted_gap",
]


class FlowScriptRecord(BaseModel):
    """Lifecycle of one emitted script inside one split folder.

    ``script_index`` 0 is the split's primary script; higher indices are
    the extra scripts the verify agent requested to cover paths the
    primary could not.
    """

    script_index: int = Field(default=0, ge=0, description="0 = primary script; 1.. = verify-requested extra scripts.")
    status: ScriptStatus = "pending"
    attempts: int = 0
    script_path: str = ""
    script_hash: str = Field(default="", description="md5 of the emitted .py — detects repair stagnation.")
    emits: int = 0
