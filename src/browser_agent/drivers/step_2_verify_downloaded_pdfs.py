"""Top-level driver for the download-verification agent (step 2).

Reads the active run from ``active_run.yaml``, builds a
:class:`VerificationAgentDeps` with a :class:`ZendriverBrowserSession`,
the run's ``metadata.db`` + ``downloads/`` paths, and a
:class:`SubprocessReadScriptRunner`, constructs a
:class:`VerificationRequest` from the run prompt + the latest step 0
discovery + processing scripts + a gap map of the DB, runs the
:class:`VerifyDownloadsUseCase` with the ``minimax-m3:cloud`` model,
and writes ``verification_report.md`` into the run directory. The
discovery script (``<date>__discover__<slug>.py``) populates the
``discovered_links`` table; the processing script consumes it.

Usage:
    python -m browser_agent.drivers.step_2_verify_downloaded_pdfs
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.subprocess_read_script_runner import (
    SubprocessReadScriptRunner,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.configuration import (
    VERIFICATION_MODEL,
    VERIFICATION_PDF_COUNT,
    VERIFICATION_QUERY_LIMIT,
    VERIFICATION_SCRIPT_RUN_LIMIT,
    ZENDRIVER_HEADLESS,
)
from browser_agent.domain.corpus_finding import CorpusFinding
from browser_agent.domain.probe_result import ProbeResult, ProbeVerdict
from browser_agent.domain.probe_verification_report import ProbeVerificationReport
from browser_agent.domain.run_config import RunConfig
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.metadata_db import parse_row_data, query_rows
from browser_agent.use_cases.reconcile_downloads_use_case import ReconcileDownloadsUseCase
from browser_agent.use_cases.reconciler_report_writer import ReconcilerReportWriter
from browser_agent.use_cases.probe_corpus_verifier import ProbeCorpusVerifier
from browser_agent.use_cases.probe_verification_report_writer import ProbeVerificationReportWriter
from browser_agent.use_cases.scraping_gap_map_builder import ScrapingGapMapBuilder
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
from browser_agent.use_cases.verification_report_writer import VerificationReportWriter
from browser_agent.use_cases.verify_downloads_use_case import VerifyDownloadsUseCase

SCRIPTS_DIRNAME = "scripts"
METADATA_SAMPLE_LIMIT = 25
SHORT_URL_LIMIT = 80

# Filename token marking a discovery script (mirrors step_1's split logic).
_DISCOVER_TOKEN = "__discover"


class VerifyDownloadsDriver:
    """End-to-end driver: run the verification agent, write the report."""

    def run(self) -> int:
        """Configure logging, run the async pipeline, return the exit code."""
        configure_logging()
        return asyncio.run(self._run_async())

    async def _run_async(self) -> int:
        """Load the active run, reconcile, run agent, write report, return exit code."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.resolve_active_path()
        logger.info("verification driver starting run={run}", run=run.name)
        discovery_path, processing_path = self._latest_script_paths(run_path)
        if processing_path is None:
            return 2
        discovery_script = discovery_path.read_text(encoding="utf-8") if discovery_path else ""
        processing_script = processing_path.read_text(encoding="utf-8")
        try:
            reconciler_section, per_row, findings = self._run_reconciler(run_path)
            deps = self._build_deps(run_path)
            request = self._build_request(
                run,
                discovery_script,
                processing_script,
                run_path,
                reconciler_section,
            )
            model = OllamaAdapter(model=VERIFICATION_MODEL).get_model()
            report = await VerifyDownloadsUseCase(deps, model).execute(request)
        except Exception as exc:
            logger.error("verification could not run: {exc}", exc=exc)
            return 2
        probe_report = self._run_probe_verification(run, run_path)
        if probe_report is not None:
            report.probe_results = probe_report.results
            ProbeVerificationReportWriter(run_path).write_json(probe_report)
        path = VerificationReportWriter(run_path).write(report)
        self._append_metadata_sample(run_path, path)
        self._print_summary(report, per_row, findings)
        logger.info("verification report written to {path}", path=path)
        return self._exit_code(report)

    def _run_reconciler(self, run_path: Path) -> tuple[str, list[ReconciledPdf], list[CorpusFinding]]:
        """Run the deterministic reconciler; return (section, per_row, findings)."""
        db_path = run_path / "metadata.db"
        downloads_path = run_path / "downloads"
        per_row, findings = ReconcileDownloadsUseCase(db_path, downloads_path).reconcile()
        md_path, json_path = ReconcilerReportWriter(run_path).write(per_row, findings)
        logger.info("reconciler inventory written to {path}", path=md_path)
        logger.info("reconciler json written to {path}", path=json_path)
        section = ReconcilerReportWriter(run_path).render_section(per_row, findings)
        return section, per_row, findings

    def _run_probe_verification(self, run: RunConfig, run_path: Path) -> ProbeVerificationReport | None:
        """Verify ``run.expected_source_urls`` against the DB; None when none declared."""
        if not run.expected_source_urls:
            logger.info("no expected_source_urls in prompt; skipping probe verification")
            return None
        return ProbeCorpusVerifier(run_path / "metadata.db").verify(run.expected_source_urls)

    @staticmethod
    def _exit_code(report: VerificationReport) -> int:
        """0 clean, 1 gaps found, 2 could not run."""
        if report.missing_count > 0 or report.missing_coverage:
            return 1
        if any(r.verdict is not ProbeVerdict.CAPTURED for r in report.probe_results):
            return 1
        return 0

    def _build_deps(self, run_path: Path) -> VerificationAgentDeps:
        """Wire the browser session, DB/downloads paths, and script runner into deps."""
        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )
        return VerificationAgentDeps(
            browser_session=session,
            db_path=run_path / "metadata.db",
            downloads_path=run_path / "downloads",
            script_runner=SubprocessReadScriptRunner(),
            pdf_check_limit=VERIFICATION_PDF_COUNT,
            query_db_limit=VERIFICATION_QUERY_LIMIT,
            script_run_limit=VERIFICATION_SCRIPT_RUN_LIMIT,
        )

    def _build_request(
        self,
        run,
        discovery_script: str,
        processing_script: str,
        run_path: Path,
        reconciler_section: str,
    ) -> VerificationRequest:
        """Build the request from the run prompt, both scripts, gap map, and reconciler."""
        gap_map = ScrapingGapMapBuilder(run_path / "metadata.db").build()
        return VerificationRequest(
            task_prompt=run.prompt,
            discovery_script=discovery_script,
            processing_script=processing_script,
            gap_map=gap_map,
            reconciler_inventory=reconciler_section,
        )

    def _latest_script_paths(self, run_path: Path) -> tuple[Path | None, Path | None]:
        """Return ``(discovery_path, processing_path)`` — newest of each, or None.

        ``__discover__`` in the name → discovery; every other dated
        ``YYYY_MM_DD_*.py`` (excluding ``.raw.py``) → processing. Mirrors
        step_1's split so the agent sees both scripts of a two-script run.
        """
        scripts_dir = run_path / SCRIPTS_DIRNAME
        if not scripts_dir.is_dir():
            logger.warning("no scripts directory at {dir}", dir=scripts_dir)
            return None, None
        dated = [
            p for p in scripts_dir.glob("*.py") if re.match(r"\d{4}_\d{2}_\d{2}", p.name) and not p.name.endswith(".raw.py")
        ]
        discovery = sorted((p for p in dated if _DISCOVER_TOKEN in p.name), key=lambda p: p.stat().st_mtime)
        processing = sorted((p for p in dated if _DISCOVER_TOKEN not in p.name), key=lambda p: p.stat().st_mtime)
        if not processing:
            logger.warning("no step 0 processing script found in {dir}", dir=scripts_dir)
        return (discovery[-1] if discovery else None), (processing[-1] if processing else None)

    @staticmethod
    def _print_summary(
        report: VerificationReport,
        per_row: list[ReconciledPdf],
        findings: list[CorpusFinding],
    ) -> None:
        """Print a scope-separated summary so the two scopes cannot be confused."""
        _log_inventory(per_row, findings)
        _log_spot_checks(report)
        _log_coverage(report)
        if report.probe_results:
            _log_probes(report.probe_results)

    def _append_metadata_sample(self, run_path: Path, report_path: Path) -> None:
        """Append the first N metadata.db rows as a sample section to the report."""
        rows = query_rows(run_path / "metadata.db")[:METADATA_SAMPLE_LIMIT]
        section = self._render_metadata_sample(rows)
        with report_path.open("a", encoding="utf-8") as fh:
            fh.write("\n\n" + section)

    @staticmethod
    def _render_metadata_sample(rows: list[tuple[str, str, str]]) -> str:
        """Render the first-N metadata rows as a markdown table for the report."""
        header = (
            "## Metadata Sample (first 25 rows)\n\n"
            "| # | task_slug | file_url | pdf_filename | source_url |\n"
            "| --- | --- | --- | --- | --- |"
        )
        lines = []
        for idx, row in enumerate(rows, start=1):
            source_url, slug, data_json = row
            data = parse_row_data(data_json)
            file_url = _short(data.get("file_url", ""))
            pdf_filename = _short(data.get("pdf_filename", ""))
            lines.append(f"| {idx} | {slug} | {file_url} | {pdf_filename} | {_short(source_url)} |")
        return header + ("\n" + "\n".join(lines) if lines else "")


def _row_counts(per_row: list[ReconciledPdf]) -> dict[str, int]:
    """Tally reconciler verdicts across all DB rows."""
    counts = {"total": len(per_row), "present": 0, "missing": 0, "corrupt": 0, "small": 0}
    for r in per_row:
        if r.verdict == "present":
            counts["present"] += 1
        elif r.verdict == "file_not_downloaded":
            counts["missing"] += 1
        elif r.verdict == "corrupt_file":
            counts["corrupt"] += 1
        elif r.verdict == "suspiciously_small":
            counts["small"] += 1
    return counts


def _log_inventory(per_row: list[ReconciledPdf], findings: list[CorpusFinding]) -> None:
    """Log the ground-truth DB-vs-disk inventory (all rows scope)."""
    c = _row_counts(per_row)
    logger.info(
        "DB vs disk (ground truth, all rows): rows={} present={} missing={} corrupt={} small={} dup_urls={} orphans={}",
        c["total"],
        c["present"],
        c["missing"],
        c["corrupt"],
        c["small"],
        _finding_count(findings, "duplicate_pdf_url"),
        _finding_count(findings, "orphan_file"),
    )


def _log_spot_checks(report: VerificationReport) -> None:
    """Log the agent spot-check scope (new candidates only)."""
    logger.info(
        "Agent spot-checks (new candidates only): checked={} missing_count={}",
        len(report.pdf_results),
        report.missing_count,
    )


def _log_coverage(report: VerificationReport) -> None:
    """Log the coverage scope (expected vs observed)."""
    logger.info(
        "Coverage: expected={} observed={} complete={} missing_paths={}",
        report.expected_pdf_total,
        report.observed_pdf_total,
        report.coverage_complete,
        len(report.missing_coverage),
    )


def _log_probes(results: list[ProbeResult]) -> None:
    """Log the probe corpus summary + per-failure detail (capped at 20)."""
    total = len(results)
    captured = sum(1 for r in results if r.verdict is ProbeVerdict.CAPTURED)
    logger.info("Probe corpus: total={} captured={} failed={}", total, captured, total - captured)
    logged = 0
    for r in results:
        if r.verdict is ProbeVerdict.CAPTURED:
            continue
        if logged >= 20:
            logger.info("  ... {} more probe failures omitted", total - captured - logged)
            break
        logger.info("  probe FAIL {url} {verdict} {notes}", url=r.source_url, verdict=r.verdict.value, notes=r.notes)
        logged += 1


def _finding_count(findings: list[CorpusFinding], kind: str) -> int:
    """Return how many items the corpus finding of ``kind`` lists (0 if absent)."""
    for f in findings:
        if f.kind == kind:
            return len(f.items)
    return 0


def _short(text: str) -> str:
    """Truncate a long string for table display."""
    if len(text) <= SHORT_URL_LIMIT:
        return text
    return text[: SHORT_URL_LIMIT - 3] + "..."


def main() -> None:
    """Module entry point."""
    raise SystemExit(VerifyDownloadsDriver().run())


if __name__ == "__main__":
    main()
