"""Linter for the flow's emitted scripts: legacy checks + shared-store gate.

The legacy :class:`EmittedScriptLinter` runs unchanged (its
``_PROCESSING_CHECKS`` — discovery is gone in the flow, so processing
checks are the whole set). One flow-specific error is added: the script
lives inside ``<split>/scripts/``, but its downloads and metadata rows
belong to the SHARED run root. A script that computes ``out_dir`` from
``__file__`` would write into the split folder — the flow injects
``BROWSER_AGENT_*`` env vars and the script must use
``Path(__file__).resolve().parent.parent.parent / "downloads"``
(three levels up: scripts → split → run root). The gate accepts exactly
that shape or an env-var indirection.
"""

from __future__ import annotations

import re

from browser_agent.domain.lint_finding import LintFinding
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter

_SHARED_STORE_MSG = (
    "flow scripts live inside <split>/scripts/ but downloads and metadata.db are SHARED "
    "at the run root. The driver sets BROWSER_AGENT_SAVE_RECORD_DB_PATH and runs the script "
    "with the run root as its working directory; compute out_dir as "
    'Path(__file__).resolve().parent.parent.parent / "downloads" '
    "(scripts → split folder → run root), never parent.parent alone (that is the SPLIT "
    "folder, which would create a private downloads/ inside the split)."
)

# ``parent.parent / "downloads"`` (two levels) — the legacy layout — is a
# private split-local downloads dir in the flow layout. Three levels is the
# shared run root. The gate matches Path(...) expressions containing exactly
# two ``.parent`` references before a "downloads" literal.
_TWO_PARENT_DOWNLOADS = re.compile(
    r"""Path\(\s*__file__\s*\)\s*\.resolve\(\)\s*\.parent\s*\.parent\s*/\s*["']downloads["']"""
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
        """Flag a two-level ``__file__``-relative downloads path (split-local)."""
        out: list[LintFinding] = []
        for match in _TWO_PARENT_DOWNLOADS.finditer(python_code):
            out.append(
                LintFinding(
                    rule="flow-shared-store",
                    severity="error",
                    message=_SHARED_STORE_MSG,
                    line=python_code.count("\n", 0, match.start()) + 1,
                )
            )
        return out
