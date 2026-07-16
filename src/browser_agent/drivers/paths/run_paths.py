"""Resolve the active run's filesystem layout.

Hides the ``RUNS_PATH / <name>`` + per-run sub-directory dance
behind one object so the Uwazi drivers never reimplement it.
Each method returns a concrete path; sub-directories are
``mkdir(parents=True, exist_ok=True)``-ed on access so callers
can immediately write into them.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.configuration import MAPPINGS_DIRNAME, THESAURI_MAPPINGS_DIRNAME


class RunPaths:
    """Resolve the active run's directory and its standard sub-directories."""

    def __init__(self) -> None:
        self._root = self._ensure(self._root_path())

    def _root_path(self) -> Path:
        """Return the root path for the active run from :class:`RunsConfigLoader`."""
        return RunsConfigLoader.load_active_path()

    def _ensure(self, path: Path) -> Path:
        """Return ``path`` after creating it (and parents) on disk."""
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_path(self) -> Path:
        """Return the root path of the active run."""
        return self._root

    def metadata_db_path(self) -> Path:
        """Return the active run's ``metadata.db`` path."""
        return self._root / "metadata.db"

    def mappings_dir(self) -> Path:
        """Return the active run's ``mappings/`` directory, created on disk."""
        return self._ensure(self._root / MAPPINGS_DIRNAME)

    def thesauri_mappings_dir(self) -> Path:
        """Return the active run's ``thesauri_mappings/`` directory, created on disk."""
        return self._ensure(self._root / THESAURI_MAPPINGS_DIRNAME)

    def default_mapping_path(self) -> Path:
        """Return the canonical ``mappings/uwazi_mapping.yaml`` for the active run."""
        return self.mappings_dir() / "uwazi_mapping.yaml"

    def downloads_dir(self) -> Path:
        """Return the active run's ``downloads/`` directory, created on disk."""
        return self._ensure(self._root / "downloads")

    def default_thesaurus_path(self, thesaurus_name: str) -> Path:
        """Return the canonical ``thesauri_mappings/<name>.yaml`` path."""
        safe = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in thesaurus_name)
        return self.thesauri_mappings_dir() / f"{safe}.yaml"
