"""Linter for the flow's emitted scripts: legacy checks + shared-store gate.

The legacy :class:`EmittedScriptLinter` runs unchanged (its
``_PROCESSING_CHECKS`` — discovery is gone in the flow, so processing
checks are the whole set). One flow-specific error is added: the script
lives inside ``<split>/scripts/``, but its downloads and metadata rows
belong to the SHARED run root. A script that computes ``out_dir`` from
``__file__`` would write into the split folder — the flow injects
``BROWSER_AGENT_*`` env vars and the script must use
``Path(__file__).resolve().parent.parent.parent.parent / "downloads"``
(four levels up: scripts → split folder → flow → run root). The gate accepts
exactly that shape or an env-var indirection.
"""

from __future__ import annotations

import re

from browser_agent.domain.lint_finding import LintFinding
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter

_SHARED_STORE_MSG = (
    "flow scripts live inside <split>/scripts/ but downloads and metadata.db are SHARED "
    "at the run root. The driver sets BROWSER_AGENT_SAVE_RECORD_DB_PATH and runs the script "
    "with the run root as its working directory; compute out_dir as "
    'Path(__file__).resolve().parent.parent.parent.parent / "downloads" '
    "(scripts → split folder → flow → run root, FOUR levels). Never use two or three "
    ".parent levels: two resolves to the split folder, three to flow/ — both create a "
    "private downloads/ the shared verifier cannot see."
)

# ``parent.parent / "downloads"`` (two levels) and
# ``parent.parent.parent / "downloads"`` (three levels) both miss the shared
# run root: two resolves to the split folder, three to flow/. Four levels
# (scripts → split → flow → run root) is correct. The gate matches Path(...)
# expressions with two or three ``.parent`` references before "downloads".
_NON_RUN_ROOT_DOWNLOADS = re.compile(
    r"""Path\(\s*__file__\s*\)\s*\.resolve\(\)\s*(?:\.parent\s*){2,3}/\s*["']downloads["']"""
)


class FlowScriptLinter:
    """Lint a flow-emitted script: legacy processing checks + shared-store gate."""

    def __init__(self, require_html_files: bool = False) -> None:
        self._legacy: EmittedScriptLinter = EmittedScriptLinter(require_html_files=require_html_files)

    def lint(self, python_code: str) -> list[LintFinding]:
        """Return every error-severity finding for one flow script."""
        findings = [f for f in self._legacy.lint(python_code, kind="processing") if f.severity == "error"]
        findings.extend(self._shared_store_findings(python_code))
        return findings

    @staticmethod
    def _shared_store_findings(python_code: str) -> list[LintFinding]:
        """Flag a non-run-root ``__file__``-relative downloads path (2-3 levels)."""
        out: list[LintFinding] = []
        for match in _NON_RUN_ROOT_DOWNLOADS.finditer(python_code):
            out.append(
                LintFinding(
                    rule="flow-shared-store",
                    severity="error",
                    message=_SHARED_STORE_MSG,
                    line=python_code.count("\n", 0, match.start()) + 1,
                )
            )
        return out
