"""Write the validation report markdown to the run directory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from browser_agent.domain.pdf_check_result import PdfCheckResult
from browser_agent.domain.validation_report import ValidationReport

_REPORT_FILENAME = "validation_report.md"


class ScrapingReportWriter:
    """Write :class:`ValidationReport` as ``validation_report.md``."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def write(self, report: ValidationReport) -> Path:
        """Write the report and return the path written."""
        path = self._run_path / _REPORT_FILENAME
        _ = path.write_text(self._render(report), encoding="utf-8")
        logger.info("validation report written to {path}", path=path)
        return path

    def _render(self, report: ValidationReport) -> str:
        """Render the full markdown report."""
        lines = [self._header(), self._summary(report), self._table(report)]
        lines.append(self._section("Overall Assessment", report.overall_assessment))
        lines.append(self._section("Recommendations", report.recommendations))
        return "\n\n".join(lines)

    def _header(self) -> str:
        """Return the report header with timestamp."""
        stamp = datetime.now().isoformat(timespec="seconds")
        return f"# Scraping Validation Report\n\nGenerated: {stamp}"

    def _summary(self, report: ValidationReport) -> str:
        """Return the summary section with counts."""
        total = len(report.pdf_results)
        present = sum(1 for r in report.pdf_results if r.verdict == "present")
        missing = report.missing_count
        corrupt = sum(1 for r in report.pdf_results if r.verdict == "corrupt_file")
        return f"## Summary\n\n- Total checked: {total}\n- Present: {present}\n- Missing: {missing}\n- Corrupt: {corrupt}"

    def _table(self, report: ValidationReport) -> str:
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

    def _section(self, title: str, body: str) -> str:
        """Return a titled markdown section."""
        return f"## {title}\n\n{body}"
