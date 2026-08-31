"""Write one folder per TaskSplit under a run's flow/ directory."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from browser_agent.domain.discover_plan import DiscoverPlan
from browser_agent.domain.task_split import TaskSplit


class SplitFolderWriter:
    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def write(self, plan: DiscoverPlan, start_order: int, existing_names: set[str]) -> list[str]:
        created: list[str] = []
        for i, split in enumerate(plan.splits):
            if split.folder_name in existing_names:
                logger.error("split folder_name={n} already exists — skipping", n=split.folder_name)
                continue
            order = start_order + i
            created.append(self._write_split(split, order))
            existing_names.add(split.folder_name)
        self._append_log(created, self._new_paths(plan, created), plan.discoverer_notes)
        return created

    def _write_split(self, split: TaskSplit, order: int) -> str:
        folder = f"{order}_{split.folder_name}"
        folder_dir = self._run_path / "flow" / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(folder_dir / "prompt.md", split.prompt)
        data = split.model_dump(mode="json")
        data["order"] = order
        data["folder"] = folder
        _atomic_write(folder_dir / "split.json", json.dumps(data, indent=2, ensure_ascii=False))
        return folder

    def _append_log(self, created: list[str], new_paths: list[str], notes: str = "") -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "created_folders": created,
            "new_paths": new_paths,
            "discoverer_notes": notes,
        }
        log_path = self._run_path / "flow" / "discovery_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            _ = fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _new_paths(self, plan: DiscoverPlan, created: list[str]) -> list[str]:
        names = {name.split("_", 1)[1] for name in created}
        return [p for split in plan.splits if split.folder_name in names for p in split.covered_paths]


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` (crash-safe)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
