"""Statically verify ``expected_source_urls`` against the active run's metadata.db.

Deterministic, LLM-free check: for every URL in the active run's
``expected_source_urls``, assert the scraper captured it as a row in
``metadata.db``. Findings are printed to the terminal and persisted as
``probe_verification_report.json`` in the run folder.

Usage:
    python -m browser_agent.drivers.step_1_verify_expected_sources
"""

from __future__ import annotations
from pathlib import Path

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.domain.run_config import RunConfig
from browser_agent.drivers.console.expected_sources_console import ExpectedSourcesConsole
from browser_agent.use_cases.probe_corpus_verifier import ProbeCorpusVerifier
from browser_agent.use_cases.probe_verification_report_writer import ProbeVerificationReportWriter


class VerifyExpectedSourcesDriver:
    """End-to-end driver: load run -> probe DB -> print + persist report."""

    def run(self) -> int:
        """Module entry: run the static verification and return the exit code."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        console = ExpectedSourcesConsole()
        if not run.expected_source_urls:
            console.print_no_urls(run.name)
            return 0
        db_path = run_path / "metadata.db"
        if not db_path.exists():
            console.print_no_db(db_path)
            return 1
        return self._verify(run, run_path, db_path, console)

    def _verify(self, run: RunConfig, run_path: Path, db_path: Path, console: ExpectedSourcesConsole) -> int:
        """Run the probe, persist the report, print findings, return exit code."""
        report = ProbeCorpusVerifier(db_path).verify(run.expected_source_urls)
        writer = ProbeVerificationReportWriter(run_path)
        json_path = writer.write_json(report)
        console.print_report(report, writer.render_section(report), json_path)
        return 1 if report.counts()["failed"] else 0


def main() -> None:
    """Module entry point: invoke the driver and set the process exit code."""
    raise SystemExit(VerifyExpectedSourcesDriver().run())


if __name__ == "__main__":
    main()
