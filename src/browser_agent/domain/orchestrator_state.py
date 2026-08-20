"""The full mutable state of one flow run — persisted as state.json."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.scrape_plan import ScrapePlan
from browser_agent.domain.subtask_record import SubtaskRecord


class OrchestratorState(BaseModel):
    """Serialisable state of the flow orchestrator — resume-safe."""

    plan: ScrapePlan
    plan_counter: int = Field(default=1, ge=1)
    replans: int = Field(default=0, ge=0)
    records: list[SubtaskRecord] = Field(default_factory=list)
    current_subtask_id: str = ""
    finished: bool = False
