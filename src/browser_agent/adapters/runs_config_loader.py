from __future__ import annotations

from pathlib import Path

import yaml

from browser_agent.configuration import RUNS_FILE, RUNS_PATH, RUN_CONFIG_FILENAME
from browser_agent.domain.run_config import RunConfig


class RunsConfigLoader:
    """Parse ``runs.yaml`` for the active run name, then load the
    per-run configuration from ``data/runs/<active_run>/config.yaml``.

    The top-level YAML carries only the ``active_run`` name. The
    per-run YAML carries the ``template``, ``prompt``, and optional
    Uwazi-mapping fields that define a :class:`RunConfig`.
    """

    @staticmethod
    def load_active() -> RunConfig:
        """Return the active :class:`RunConfig` from the per-run config file."""
        active_name = _load_active_name()
        return _load_run_config(active_name)

    @staticmethod
    def load_active_path() -> Path:
        """Return the directory path for the active run, created on disk."""
        active_name = _load_active_name()
        return _run_path(active_name)


def _load_active_name() -> str:
    """Return the ``active_run`` name from ``runs.yaml``."""
    if not RUNS_FILE.is_file():
        raise FileNotFoundError(f"runs config not found at {RUNS_FILE}")
    data = yaml.safe_load(RUNS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "active_run" not in data:
        raise ValueError(f"runs.yaml must contain an 'active_run' key (got {data!r})")
    return str(data["active_run"])


def _load_run_config(name: str) -> RunConfig:
    """Load a :class:`RunConfig` from ``data/runs/<name>/config.yaml``."""
    config_path = RUNS_PATH / name / RUN_CONFIG_FILENAME
    if not config_path.is_file():
        raise FileNotFoundError(f"per-run config not found at {config_path} (run {name!r} has no {RUN_CONFIG_FILENAME})")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return RunConfig.model_validate({"name": name, **data})


def _run_path(name: str) -> Path:
    path = RUNS_PATH / name
    path.mkdir(parents=True, exist_ok=True)
    return path
