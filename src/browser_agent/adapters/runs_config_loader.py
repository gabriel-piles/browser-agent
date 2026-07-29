"""Parse ``active_run.yaml`` for the active run name, then load the
per-run configuration from ``data/prompts/<active_run>.yaml``.

The top-level YAML carries only the ``active_run`` name. The
prompt YAML carries the ``template``, ``prompt``, and optional
Uwazi-mapping fields that define a :class:`RunConfig`.  When the
active run path is accessed the prompt YAML is copied verbatim
into the run folder (keeping its ``<name>.yaml`` filename) so
each run folder carries a historical snapshot of the prompt as
it was at execution time.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from browser_agent.configuration import (
    PROMPTS_PATH,
    RUNS_FILE,
    RUNS_PATH,
)
from browser_agent.domain.run_config import RunConfig


class RunsConfigLoader:
    """Parse ``active_run.yaml`` for the active run name, then load the
    per-run configuration from ``data/prompts/<active_run>.yaml``.

    The top-level YAML carries only the ``active_run`` name. The
    prompt YAML carries the ``template``, ``prompt``, and optional
    Uwazi-mapping fields that define a :class:`RunConfig`.
    """

    @staticmethod
    def load_active() -> RunConfig:
        """Return the active :class:`RunConfig` from the prompt YAML."""
        active_name = _load_active_name()
        return _load_run_config(active_name)

    @staticmethod
    def resolve_active_path() -> Path:
        """Return the directory path for the active run, created on disk.

        Unlike :meth:`load_active_path`, this does NOT copy the prompt
        snapshot — the caller expects the prompt was already copied by
        step 0.
        """
        active_name = _load_active_name()
        path = RUNS_PATH / active_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def load_active_path() -> Path:
        """Return the directory path for the active run, created on disk.

        The prompt YAML from ``data/prompts/<active_name>.yaml`` is
        copied verbatim into the run folder (keeping its
        ``<active_name>.yaml`` filename, overwriting any previous
        snapshot) so each run folder records the exact prompt
        state at execution time.
        """
        active_name = _load_active_name()
        return _run_path(active_name)

    @staticmethod
    def load_run_config_from_run() -> RunConfig:
        """Load :class:`RunConfig` from the prompt copy inside the run folder.

        The prompt YAML must already exist in the run folder (copied
        there by step 0 via :meth:`load_active_path`).  Raises
        :class:`FileNotFoundError` when the copy is missing.
        """
        active_name = _load_active_name()
        run_path = RUNS_PATH / active_name
        config_path = run_path / f"{active_name}.yaml"
        if not config_path.is_file():
            raise FileNotFoundError(
                f"No prompt copy found at {config_path}. Run step 0 first to copy the prompt into the run folder."
            )
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return RunConfig.model_validate({"name": active_name, **data})


def _load_active_name() -> str:
    """Return the ``active_run`` name from ``active_run.yaml``.

    Strips a trailing ``.yaml`` extension if present so callers
    can write the value with or without the suffix.
    """
    if not RUNS_FILE.is_file():
        raise FileNotFoundError(f"runs config not found at {RUNS_FILE}")
    data = yaml.safe_load(RUNS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "active_run" not in data:
        raise ValueError(f"active_run.yaml must contain an 'active_run' key (got {data!r})")
    return str(data["active_run"]).removesuffix(".yaml")


def _load_run_config(name: str) -> RunConfig:
    """Load a :class:`RunConfig` from ``data/prompts/<name>.yaml``."""
    config_path = PROMPTS_PATH / f"{name}.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"prompt config not found at {config_path} (run {name!r} has no prompt YAML)")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return RunConfig.model_validate({"name": name, **data})


def _run_path(name: str) -> Path:
    """Create the run directory and copy the prompt YAML snapshot into it."""
    path = RUNS_PATH / name
    path.mkdir(parents=True, exist_ok=True)
    _copy_prompt_snapshot(name, path)
    return path


def _copy_prompt_snapshot(name: str, run_path: Path) -> None:
    """Copy ``data/prompts/<name>.yaml`` → ``run_path/<name>.yaml``."""
    prompt_yaml = PROMPTS_PATH / f"{name}.yaml"
    if prompt_yaml.is_file():
        _ = shutil.copy2(prompt_yaml, run_path / prompt_yaml.name)
