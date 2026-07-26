"""Dependency object for the download-verification agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from browser_agent.configuration import VERIFICATION_PDF_COUNT, VERIFICATION_SCRIPT_RUN_LIMIT
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.ports.read_script_runner_port import ReadScriptRunnerPort


@dataclass
class VerificationAgentDeps:
    """Dependency object for the verification agent.

    Carries the browser session (for ``explore_page``), the run's
    ``metadata.db`` and ``downloads/`` paths (for ``check_pdf`` and
    ``query_db``), the read-only script runner (for ``run_read_script``),
    and counter/limit pairs that cap how many PDF checks and how many
    forensic script runs one agent turn may perform.
    """

    browser_session: BrowserSessionPort
    db_path: Path
    downloads_path: Path
    script_runner: ReadScriptRunnerPort
    pdf_checks: int = 0
    pdf_check_limit: int = VERIFICATION_PDF_COUNT
    script_runs: int = 0
    script_run_limit: int = VERIFICATION_SCRIPT_RUN_LIMIT
