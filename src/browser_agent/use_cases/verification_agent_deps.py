"""Dependency object for the download-verification agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from browser_agent.configuration import (
    MAX_EXPLORE_CALLS,
    VERIFICATION_PDF_COUNT,
    VERIFICATION_QUERY_LIMIT,
    VERIFICATION_SCRIPT_RUN_LIMIT,
)
from browser_agent.domain.explore_duplicate_guard import ExploreDuplicateGuard
from browser_agent.domain.expected_path import ExpectedPath
from browser_agent.domain.pdf_check_result import PdfCheckResult
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.ports.read_script_runner_port import ReadScriptRunnerPort


@dataclass
class VerificationAgentDeps:
    """Dependency object for the verification agent.

    Carries the browser session (for ``explore_page``), the run's
    ``metadata.db`` and ``downloads/`` paths (for ``check_pdf`` and
    ``query_db``), the read-only script runner (for ``run_read_script``),
    and counter/limit pairs that cap how many PDF checks, SQL queries,
    and forensic script runs one agent turn may perform. ``pdf_results``
    accumulates the real :class:`PdfCheckResult` objects so the driver
    can splice them into the report without the LLM re-transcribing them.
    """

    browser_session: BrowserSessionPort
    db_path: Path
    downloads_path: Path
    script_runner: ReadScriptRunnerPort
    pdf_checks: int = 0
    pdf_check_limit: int = VERIFICATION_PDF_COUNT
    script_runs: int = 0
    script_run_limit: int = VERIFICATION_SCRIPT_RUN_LIMIT
    query_db_calls: int = 0
    query_db_limit: int = VERIFICATION_QUERY_LIMIT
    pdf_results: list[PdfCheckResult] = field(default_factory=list)
    declared_paths: list[ExpectedPath] = field(default_factory=list)
    explore_calls: int = 0
    explore_limit: int = MAX_EXPLORE_CALLS
    empty_result_streak: int = 0
    last_analyze_selectors: list[str] = field(default_factory=list)
    explore_guard: ExploreDuplicateGuard = field(default_factory=ExploreDuplicateGuard)
