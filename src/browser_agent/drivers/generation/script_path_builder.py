"""Compute the on-disk path the generated script is written to.

Hides the date/slug arithmetic behind a single object so the
driver script does not have to know the directory layout. The
output path is always under ``<run_path>/scripts/`` and uses
the date as a prefix so a single day does not overwrite its
peers.
"""

from __future__ import annotations

import datetime
from pathlib import Path


class ScriptPathBuilder:
    """Return a per-day, per-slug path under the run's ``scripts/`` directory."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def build(self, task: str) -> Path:
        """Return the path the script for ``task`` is written to today."""
        today = self._today()
        slug = self._slug(task)
        scripts_dir = self._scripts_dir()
        return scripts_dir / f"{today}__{slug}.py"

    def _today(self) -> str:
        """Return today's date as ``YYYY_MM_DD`` for the filename prefix."""
        return datetime.date.today().strftime("%Y_%m_%d")

    def _slug(self, task: str) -> str:
        """Return a filesystem-safe slug derived from the first words of ``task``."""
        words = task.split()
        first_words = "_".join(words[:6]) if len(words) >= 6 else "_".join(words)
        slug = "".join(c if c.isalnum() else "_" for c in first_words.lower())
        return slug.strip("_") or "generated"

    def _scripts_dir(self) -> Path:
        """Return the run's ``scripts/`` directory, created on disk."""
        scripts_dir = self._run_path / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return scripts_dir
