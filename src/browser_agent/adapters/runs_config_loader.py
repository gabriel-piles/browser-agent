from __future__ import annotations

from pathlib import Path

import yaml

from browser_agent.configuration import RUNS_FILE, RUNS_PATH
from browser_agent.domain.run_config import RunConfig
from browser_agent.domain.runs_config import RunsConfig


class RunsConfigLoader:
    """Parse ``runs.yaml`` and resolve the active run.

    The YAML carries an ``active_run`` name and a list of ``runs``.
    This loader reads the file, validates it into a :class:`RunsConfig`,
    and resolves the active run to a :class:`RunConfig` plus the
    directory where all artifacts (scripts, downloads, metadata.db)
    are persisted.
    """

    @staticmethod
    def load_active() -> RunConfig:
        """Return the active :class:`RunConfig` from ``runs.yaml``."""
        return _load_runs().active()

    @staticmethod
    def load_active_path() -> Path:
        """Return the directory path for the active run, created on disk."""
        run = _load_runs().active()
        return _run_path(run.name)


def _load_runs() -> RunsConfig:
    if not RUNS_FILE.is_file():
        raise FileNotFoundError(f"runs config not found at {RUNS_FILE}")
    data = yaml.safe_load(RUNS_FILE.read_text(encoding="utf-8"))
    return RunsConfig.model_validate(data)


def _run_path(name: str) -> Path:
    path = RUNS_PATH / name
    path.mkdir(parents=True, exist_ok=True)
    return path
