"""Resolve the flow/ sub-tree layout of the new step-1 flow (per-split dirs).

Each split folder created by step 0 gets a self-contained working tree:
scripts/, verification/, logs/, profile/, debug/, scratch/ — everything
but the shared ``downloads/`` and ``metadata.db`` at the run root.
"""

from __future__ import annotations

from pathlib import Path


class SplitFlowPaths:
    """Resolve the per-split artifact paths under one split folder."""

    def __init__(self, split_dir: Path) -> None:
        self._root: Path = split_dir

    def _ensure(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def split_dir(self) -> Path:
        """Return the split folder itself (created on disk)."""
        return self._ensure(self._root)

    def state_path(self) -> Path:
        """Return the per-split ``state.json`` path (parent ensured)."""
        _ = self.split_dir()
        return self._root / "state.json"

    def scripts_dir(self) -> Path:
        """Return the split's ``scripts/`` directory (created on disk)."""
        return self._ensure(self._root / "scripts")

    def verification_dir(self) -> Path:
        """Return the split's ``verification/`` directory (created on disk)."""
        return self._ensure(self._root / "verification")

    def logs_dir(self) -> Path:
        """Return the split's ``logs/`` directory (created on disk)."""
        return self._ensure(self._root / "logs")

    def debug_dir(self) -> Path:
        """Return the split's ``debug/`` directory (created on disk)."""
        return self._ensure(self._root / "debug")

    def profile_dir(self, name: str) -> Path:
        """Return a named per-split profile directory (created on disk)."""
        return self._ensure(self._root / "profile" / name)

    def scratch_dir(self) -> Path:
        """Return the split's scratch dir for smoke-test DB + validation (created on disk)."""
        return self._ensure(self._root / "scratch")

    def execution_log_path(self, script_index: int) -> Path:
        """Return the live execution log path for one script index (parent ensured)."""
        _ = self.logs_dir()
        return self._root / "logs" / f"script_{script_index}_live.log"
