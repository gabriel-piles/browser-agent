"""Resolve the flow/ sub-tree layout under a run directory.

Mirrors :class:`RunPaths` — each method returns a concrete path;
sub-directories are ``mkdir(parents=True, exist_ok=True)``-ed on access
so callers can immediately write into them.
"""

from __future__ import annotations

from pathlib import Path


class FlowPaths:
    """Resolve the flow/ report-and-state tree under one run path."""

    def __init__(self, run_path: Path) -> None:
        self._root = run_path

    def _ensure(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def flow_dir(self) -> Path:
        return self._ensure(self._root / "flow")

    def state_path(self) -> Path:
        return self.flow_dir() / "state.json"

    def decisions_path(self) -> Path:
        return self.flow_dir() / "decisions.jsonl"

    def plan_path(self, n: int) -> Path:
        return self.flow_dir() / f"plan_{n:03d}.json"

    def subtask_dir(self, subtask_id: str) -> Path:
        return self._ensure(self.flow_dir() / "subtasks" / subtask_id)

    def subtask_spec_path(self, subtask_id: str) -> Path:
        return self.subtask_dir(subtask_id) / "subtask.json"

    def subtask_report_path(self, subtask_id: str, name: str) -> Path:
        return self.subtask_dir(subtask_id) / f"{name}.json"
