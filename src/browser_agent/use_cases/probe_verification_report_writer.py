"""Render the deterministic source_url verification report as markdown + JSON sidecar.

Mirrors :class:`ReconcilerReportWriter`: ``render_section`` returns the
markdown for embedding in ``verification_report.md``; ``write_json``
writes the machine-readable ``probe_verification_report.json`` sidecar.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.probe_result import ProbeResult
from browser_agent.domain.probe_verification_report import ProbeVerificationReport

_PROBE_JSON = "probe_verification_report.json"


class ProbeVerificationReportWriter:
    """Persist the probe verification report as JSON and render markdown."""

    def __init__(self, run_path: Path) -> None:
        self._run_path: Path = run_path

    def write_json(self, report: ProbeVerificationReport) -> Path:
        """Write ``probe_verification_report.json`` and return its path."""
        json_path = self._run_path / _PROBE_JSON
        _ = json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return json_path

    def render_section(self, report: ProbeVerificationReport) -> str:
        """Return the markdown probe section, or ``""`` when no source_urls."""
        if not report.results:
            return ""
        counts = report.counts()
        lines = [
            "## Source URLs Verification (deterministic, no LLM)",
            "",
            f"captured={counts['captured']} failed={counts['failed']} total={counts['total']}",
            "",
            self._table(report),
        ]
        return "\n".join(lines)

    def _table(self, report: ProbeVerificationReport) -> str:
        """Return the per-URL markdown table."""
        header = "| source_url | verdict | matched_row |\n| --- | --- | --- |"
        rows = [self._row(r) for r in report.results]
        return header + ("\n" + "\n".join(rows) if rows else "")

    def _row(self, result: ProbeResult) -> str:
        """Return one markdown table row for a single probe result."""
        url = _short(result.source_url)
        matched = _inline(result.matched_row_source_url)
        return f"| {url} | {result.verdict.value} | {matched} |"


def _short(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _inline(text: str) -> str:
    return text.replace("|", "\\|") if text else ""
