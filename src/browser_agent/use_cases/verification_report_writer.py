"""Write the verification report markdown to the run directory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from browser_agent.domain.missing_coverage import MissingCoverage
from browser_agent.domain.pdf_check_result import PdfCheckResult
from browser_agent.domain.verification_report import VerificationReport

_REPORT_FILENAME = "verification_report.md"


class VerificationReportWriter:
    """Write :class:`VerificationReport` as ``verification_report.md``."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def write(self, report: VerificationReport) -> Path:
        """Write the report and return the path written."""
        path = self._run_path / _REPORT_FILENAME
        _ = path.write_text(self._render(report), encoding="utf-8")
        logger.info("verification report written to {path}", path=path)
        return path

    def _render(self, report: VerificationReport) -> str:
        """Render the full markdown report."""
        lines = [
            self._header(),
            self._summary(report),
            self._table(report),
            self._missing_coverage(report),
            self._section("Overall Assessment", report.overall_assessment),
            self._section("Recommendations", report.recommendations),
        ]
        return "\n\n".join(lines)

    def _header(self) -> str:
        """Return the report header with timestamp."""
        stamp = datetime.now().isoformat(timespec="seconds")
        return f"# Download Verification Report\n\nGenerated: {stamp}"

    def _summary(self, report: VerificationReport) -> str:
        """Return the summary section with counts."""
        total = len(report.pdf_results)
        present = sum(1 for r in report.pdf_results if r.verdict == "present")
        missing = report.missing_count
        corrupt = sum(1 for r in report.pdf_results if r.verdict == "corrupt_file")
        gaps = len(report.missing_coverage)
        return (
            "## Summary\n\n"
            f"- Total checked: {total}\n"
            f"- Present: {present}\n"
            f"- Missing: {missing}\n"
            f"- Corrupt: {corrupt}\n"
            f"- Missing coverage paths: {gaps}"
        )

    def _table(self, report: VerificationReport) -> str:
        """Return the per-PDF findings markdown table."""
        header = (
            "## Per-PDF Findings\n\n"
            "| URL | Verdict | In DB | File exists | File size | Notes |\n"
            "| --- | --- | --- | --- | --- | --- |"
        )
        rows = [self._table_row(r) for r in report.pdf_results]
        return header + ("\n" + "\n".join(rows) if rows else "")

    def _table_row(self, result: PdfCheckResult) -> str:
        """Return one markdown table row for a single PDF result."""
        url = self._short_url(result.url)
        size = f"{result.file_size_bytes} bytes"
        notes = result.notes.replace("|", "\\|") if result.notes else ""
        return f"| {url} | {result.verdict} | {result.found_in_db} | {result.file_exists} | {size} | {notes} |"

    def _short_url(self, url: str) -> str:
        """Truncate a long URL for table display."""
        if len(url) <= 80:
            return url
        return url[:77] + "..."

    def _missing_coverage(self, report: VerificationReport) -> str:
        """Return the Missing Coverage section."""
        if not report.missing_coverage:
            return "## Missing Coverage\n\nNo prompt-described path is missing coverage."
        blocks = [self._coverage_block(item) for item in report.missing_coverage]
        return "## Missing Coverage\n\n" + "\n\n".join(blocks)

    def _coverage_block(self, item: MissingCoverage) -> str:
        """Return one markdown block for a single MissingCoverage entry."""
        return (
            "### Path: " + _inline(item.navigation_path) + "\n\n"
            f"- Expected: {_inline(item.expected)}\n"
            f"- Actual: {_inline(item.actual)}\n"
            f"- Reason: {_inline(item.reason)}\n"
            f"- Step-0 fix: {_inline(item.step_0_fix)}"
        )

    def _section(self, title: str, body: str) -> str:
        """Return a titled markdown section."""
        return f"## {title}\n\n{body}"


def _inline(text: str) -> str:
    """Escape pipe characters for safe inline markdown."""
    return text.replace("|", "\\|") if text else ""
