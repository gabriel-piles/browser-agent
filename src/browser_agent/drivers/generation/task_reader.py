"""Read the user task from argv, stdin, or a run-level default.

Hides the argv parsing behind a single object so the top-level
driver script stays a thin flow. The class accepts the run's
default prompt as a constant (per project policy: no CLI args in
drivers, only module-level constants), and picks the right
source from the argv list the runtime passes in.
"""

from __future__ import annotations

import sys

from browser_agent.domain.run_config import RunConfig


class TaskReader:
    """Return the user task from ``--stdin``, argv, or the run's default prompt."""

    def __init__(self, default_prompt: str) -> None:
        self._default_prompt = default_prompt

    def read(self, argv: list[str], run: RunConfig) -> str:
        """Pick the task from argv/stdin, falling back to ``run.prompt``."""
        if self._wants_stdin(argv):
            return self._read_stdin(run.prompt)
        if len(argv) > 1:
            return " ".join(argv[1:]).strip()
        return run.prompt

    def _wants_stdin(self, argv: list[str]) -> bool:
        """True when the user asked the driver to consume stdin."""
        return "--stdin" in argv

    def _read_stdin(self, run_prompt: str) -> str:
        """Read the task from stdin, falling back to ``run_prompt`` on empty input."""
        return sys.stdin.read().strip() or run_prompt
