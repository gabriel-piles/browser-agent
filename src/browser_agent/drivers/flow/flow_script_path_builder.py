"""Compute the on-disk path for one flow-emitted script inside its split.

Mirrors the legacy :class:`ScriptPathBuilder` (date-prefixed, unique)
but rooted at the split's own ``scripts/`` directory instead of the
run's, so each split folder is fully self-contained.
"""

from __future__ import annotations

import datetime
from pathlib import Path


class FlowScriptPathBuilder:
    """Return a per-day, per-slug path under the split's ``scripts/`` directory."""

    def __init__(self, scripts_dir: Path) -> None:
        self._scripts_dir: Path = scripts_dir

    def build(self, name: str, script_index: int = 0) -> Path:
        """Return the path for ``name`` today; ``script_index`` distinguishes extras."""
        _ = self._scripts_dir_ensured()
        today = self._today()
        slug = self._slug(name)
        suffix = ".py" if script_index == 0 else f"_extra{script_index}.py"
        base = self._scripts_dir / f"{today}__{slug}{suffix}"
        return self._unique(base)

    @staticmethod
    def _unique(base: Path) -> Path:
        """Append ``__HHMMSS`` (then ``__002`` etc.) if ``base`` already exists."""
        if not base.exists():
            return base
        stamp = datetime.datetime.now().strftime("%H%M%S")
        stamped = base.with_name(base.stem + f"__{stamp}.py")
        if not stamped.exists():
            return stamped
        for i in range(2, 1000):
            cand = base.with_name(base.stem + f"__{i:03d}.py")
            if not cand.exists():
                return cand
        return stamped

    @staticmethod
    def _today() -> str:
        return datetime.date.today().strftime("%Y_%m_%d")

    @staticmethod
    def _slug(name: str) -> str:
        words = name.split()
        first_words = "_".join(words[:6]) if len(words) >= 6 else "_".join(words)
        slug = "".join(c if c.isalnum() else "_" for c in first_words.lower())
        return slug.strip("_") or "generated"

    def _scripts_dir_ensured(self) -> Path:
        self._scripts_dir.mkdir(parents=True, exist_ok=True)
        return self._scripts_dir
