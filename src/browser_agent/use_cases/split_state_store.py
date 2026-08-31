"""Persist and reload one split folder's :class:`SplitRunState`."""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from browser_agent.domain.split_run_state import SplitRunState


class SplitStateStore:
    """Load/save one split's state.json, plus per-split report writes."""

    def __init__(self, paths) -> None:
        self._paths: SplitFlowPathsLike = paths

    def load(self) -> SplitRunState | None:
        """Return the split's persisted state, or None when absent/corrupt."""
        path: Path = self._paths.state_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.error("split state.json corrupt — starting a fresh split (shared metadata.db/downloads are preserved)")
            return None
        return SplitRunState.model_validate(data)

    def save(self, state: SplitRunState) -> None:
        """Atomically persist ``state`` to the split's state.json."""
        _atomic_write(self._paths.state_path(), json.dumps(state.model_dump(mode="json"), indent=2))

    def write_report(self, name: str, model: BaseModel) -> None:
        """Atomically write one report model into the split's verification dir."""
        path = self._paths.verification_dir() / f"{name}.json"
        _atomic_write(path, json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False))
        logger.info("split report written: {path}", path=path)


class SplitFlowPathsLike:
    """Minimal protocol the store needs from SplitFlowPaths (structural)."""

    def state_path(self) -> Path: ...
    def verification_dir(self) -> Path: ...


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` (crash-safe)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _ = os.replace(tmp, path)
