"""Copy the canonical ``script_tools/`` package next to a run's scripts.

The generated scripts import from ``script_tools.<module>``. Python's
``sys.path[0]`` is the script's own directory, so placing a
``script_tools/`` folder beside the script resolves the imports with
zero bootstrap. The copier refreshes the copy on every run so a helper
fix takes effect on the next generation without altering old scripts.

A ``run_config.py`` is stamped into the copy with the run's absolute
profile path and the NopeCHA extension dir so ``start_browser`` picks
them up without the emitter injecting anything.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from browser_agent.adapters.nopecha_extension import NopechaExtension


class ScriptToolsCopier:
    """Copy ``script_tools/`` next to a run's scripts and stamp ``run_config.py``."""

    def copy(self, run_path: Path) -> Path:
        """Copy the tools package into ``<run>/scripts/script_tools/`` and stamp config.

        Returns the destination directory. Uses ``dirs_exist_ok=True`` so
        re-runs refresh the copy in place.
        """
        src = Path(__file__).resolve().parents[2] / "script_tools"
        dst = run_path / "scripts" / "script_tools"
        shutil.copytree(src, dst, dirs_exist_ok=True)
        self._stamp_run_config(dst, run_path)
        return dst

    @staticmethod
    def _stamp_run_config(dst: Path, run_path: Path) -> None:
        """Write ``run_config.py`` with the run's profile path and NopeCHA dir."""
        profile_path = str((run_path / "profile").resolve())
        nopecha_dir = NopechaExtension().ensure_ready()
        nopecha_repr = repr(str(nopecha_dir)) if nopecha_dir else "None"
        content = f"PROFILE_PATH = {profile_path!r}\nNOPECHA_EXTENSION_DIR = {nopecha_repr}\n"
        (dst / "run_config.py").write_text(content, encoding="utf-8")
