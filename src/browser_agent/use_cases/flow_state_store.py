"""Persist and reload the flow orchestrator's state and reports."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from browser_agent.domain.orchestrator_state import OrchestratorState


class FlowStateStore:
    """Load/save :class:`OrchestratorState`, log decisions, write per-subtask reports."""

    def __init__(self, flow_paths: Any) -> None:
        self._paths: Any = flow_paths

    def load(self) -> OrchestratorState | None:
        path = self._paths.state_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return OrchestratorState.model_validate(data)

    def save(self, state: OrchestratorState) -> None:
        path = self._paths.state_path()
        _ = path.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def log_decision(self, decision: Any, context: str) -> None:
        entry = {
            "decision": decision.model_dump(mode="json"),
            "context": context,
        }
        path = self._paths.decisions_path()
        with path.open("a", encoding="utf-8") as fh:
            _ = fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def write_report(self, subtask_id: str, name: str, model: BaseModel) -> None:
        path = self._paths.subtask_report_path(subtask_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(
            json.dumps(model.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def read_report(self, subtask_id: str, name: str, cls: type) -> Any:
        path = self._paths.subtask_report_path(subtask_id, name)
        if not path.exists():
            return None
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def write_plan(self, n: int, plan: Any) -> None:
        path = self._paths.plan_path(n)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(
            json.dumps(plan.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
