"""Read the existing split folders under a run's flow/ directory."""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from browser_agent.domain.task_split import TaskSplit

_FLOW_DIR_PATTERN = re.compile(r"^(\d+)_([a-z0-9_]+)$")

_INCREMENTAL_INSTRUCTION = (
    "Find paths/PDFs that are NEW relative to these splits — either uncovered paths "
    "or a NEW page family inside a covered scope. Emit splits ONLY for uncovered new "
    "paths/families (is_new=true); empty splits list when nothing is new. "
    "Additionally, when the last pass left UNVERIFIED pages/ranges (see its notes "
    "below), OPEN and verify them now and emit splits covering exactly the verified "
    "ranges — completing coverage across passes."
)


class SplitFolderReader:
    """Read existing TaskSplit folders from ``run_path/flow``."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def read(self) -> list[TaskSplit]:
        flow_dir = self._run_path / "flow"
        if not flow_dir.is_dir():
            return []
        splits: list[TaskSplit] = []
        for entry in sorted(flow_dir.iterdir()):
            split = self._read_folder(entry)
            if split is not None:
                splits.append(split)
        splits.sort(key=lambda split: split.order)
        return splits

    def next_order(self) -> int:
        orders = [split.order for split in self.read()]
        return max(orders) + 1 if orders else 1

    def context(self) -> str:
        splits = self.read()
        if not splits:
            return ""
        lines = ["EXISTING SPLITS (already created; do not duplicate or renumber):"]
        for split in splits:
            lines.append(
                f"- {split.order}_{split.folder_name} "
                f"(is_new={split.is_new}, page_family={split.page_family}): "
                f"covered_paths: {split.covered_paths}"
            )
            if split.format_evidence:
                lines.append(f"  format_evidence: {split.format_evidence}")
        lines.append("")
        lines.append(_INCREMENTAL_INSTRUCTION)
        notes = self.last_notes()
        if notes:
            lines.append("")
            lines.append(f"LAST PASS DISCOVERER NOTES (unverified remainder to cover now):\n{notes}")
        return "\n".join(lines)

    def last_notes(self) -> str:
        log_path = self._run_path / "flow" / "discovery_log.jsonl"
        if not log_path.is_file():
            return ""
        return _last_entry_notes(log_path)

    def _read_folder(self, entry: Path) -> TaskSplit | None:
        match = _FLOW_DIR_PATTERN.match(entry.name)
        if not match or not entry.is_dir():
            return None
        order, slug = int(match.group(1)), match.group(2)
        split_json = entry / "split.json"
        if not split_json.is_file():
            return self._synthesize(entry, slug, order)
        try:
            data = json.loads(split_json.read_text(encoding="utf-8"))
            return TaskSplit.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("corrupt split.json in {f} — skipping its content", f=entry.name)
            return self._synthesize(entry, slug, order)

    def _synthesize(self, folder: Path, slug: str, order: int) -> TaskSplit:
        prompt_path = folder / "prompt.md"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
        return TaskSplit(folder_name=slug, title=slug, prompt=prompt, order=order)


def _last_entry_notes(log_path: Path) -> str:
    """Return the discoverer_notes of the last jsonl entry (empty on any error)."""
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return ""
        return str(json.loads(lines[-1]).get("discoverer_notes") or "")
    except (OSError, json.JSONDecodeError):
        logger.warning("unreadable discovery_log.jsonl — ignoring last-pass notes")
        return ""
