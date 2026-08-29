"""Persist and reload the flow orchestrator's state and reports."""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger

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
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.error("state.json corrupt - starting a fresh plan (prior metadata.db/downloads are preserved)")
            return None
        return OrchestratorState.model_validate(data)

    def save(self, state: OrchestratorState) -> None:
        _atomic_write(self._paths.state_path(), json.dumps(state.model_dump(mode="json"), indent=2))

    def log_decision(self, decision: Any, context: str, summary: str = "") -> None:
        """Append one decision to decisions.jsonl.

        ``summary`` is the exact JSON shown to the orchestrator for that
        decision. It is persisted alongside the decision so an operator can
        reconstruct WHY the orchestrator chose an action — the decision alone
        is not auditable without the evidence it was given.
        """
        entry = {
            "decision": decision.model_dump(mode="json"),
            "context": context,
        }
        if summary:
            try:
                entry["summary"] = json.loads(summary)
            except (json.JSONDecodeError, TypeError):
                entry["summary_text"] = summary
        path = self._paths.decisions_path()
        with path.open("a", encoding="utf-8") as fh:
            _ = fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def write_report(self, subtask_id: str, name: str, model: BaseModel) -> None:
        path = self._paths.subtask_report_path(subtask_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(model.model_dump(mode="json"), indent=2))

    def read_report(self, subtask_id: str, name: str, cls: type) -> Any:
        path = self._paths.subtask_report_path(subtask_id, name)
        if not path.exists():
            return None
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def write_plan(self, n: int, plan: Any) -> None:
        path = self._paths.plan_path(n)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(plan.model_dump(mode="json"), indent=2))


def _atomic_write(path: Any, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` (crash-safe)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
