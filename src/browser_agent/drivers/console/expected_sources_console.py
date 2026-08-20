"""Console reporting for the step_1 expected-source-urls verification driver.

Keeps the human-facing print logic (status, findings table, file path)
behind one object so the driver script stays a thin flow.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.probe_verification_report import ProbeVerificationReport


class ExpectedSourcesConsole:
    """Print the human-facing progress + result for the verification driver."""

    def print_no_urls(self, run_name: str) -> None:
        """Print the message when the run defines no source URLs to check."""
        print(f"Run {run_name!r} has no 'expected_source_urls'; nothing to verify.")

    def print_no_db(self, db_path: Path) -> None:
        """Print the message when the cache file is missing."""
        print(f"No metadata.db at {db_path}. Run step_0 first.")

    def print_report(self, report: ProbeVerificationReport, markdown: str, json_path: Path) -> None:
        """Print the findings table, the summary, and the persisted file path."""
        print(markdown)
        counts = report.counts()
        print(f"\nReport written to {json_path}")
        if counts["failed"]:
            print(f"{counts['failed']} of {counts['total']} URL(s) NOT captured.")
