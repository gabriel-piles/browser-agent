"""Top-level driver class for the Zendriver script generation service.

Reads a task from argv (or the bundled default), runs the three-agent
pipeline (Explorer → Discovery Writer → Processing Writer), writes the
executable source to ``data/runs/<active_run>/scripts/<date>__<slug>.py``
for the operator to launch. The structured
:class:`GeneratedScript` (explanation, dependencies) is printed
as JSON alongside, and a sidecar ``.json`` persists the explanation,
strategy, lint findings, and smoke-test result.

Usage:
    python -m browser_agent.drivers.step_0_generate_script "<task>"
    python -m browser_agent.drivers.step_0_generate_script --stdin < task.txt
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from loguru import logger

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.generated_script_set import GeneratedScriptSet
from browser_agent.domain.smoke_test_result import SmokeTestResult
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.run_config import RunConfig
from browser_agent.domain.task_split import TaskSplit
from browser_agent.drivers.generation.prior_report_reader import PriorReportReader
from browser_agent.drivers.generation.script_emitter import ScriptEmitter
from browser_agent.drivers.generation.script_generator import ScriptGenerator
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.drivers.generation.script_smoke_tester import (
    log_smoke_test_result,
    processing_self_check,
    smoke_test_script,
)
from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier
from browser_agent.drivers.generation.discovery_audit import DiscoveryAuditor
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.logging_config import configure_logging
from browser_agent.script_tools.discovered_links_store import preseed_sample_links
from browser_agent.use_cases.concurrency_context_renderer import render_concurrency_context
from browser_agent.use_cases.discovery_manifest_parser import extract_manifest_detailed
from browser_agent.use_cases.discovery_self_check_verifier import DiscoverySelfCheckVerifier
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter
from browser_agent.use_cases.generate_discovery_script_use_case import (
    GenerateDiscoveryScriptUseCase,
)
from browser_agent.use_cases.generate_processing_script_use_case import (
    GenerateProcessingScriptUseCase,
)
from browser_agent.use_cases.script_repair_prompt import (
    format_discovery_repair,
    format_lint_repair,
    format_processing_self_check_repair,
    format_smoke_repair,
)

# Hard-coded default prompt when the operator runs the driver with
# no argv and no stdin input. Pinned here per project policy: no
# CLI args in drivers, only module-level constants.
DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."

_MAX_LINT_REPAIRS = 1
# Two repair cycles: the first repairs self-check verifier failures
# (manifest + stdout protocol + DB row count), the second repairs
# discrepancies flagged by the independent DiscoveryAuditor.
_MAX_DISCOVERY_REPAIRS = 2
_MAX_PROCESSING_REPAIRS = 3

# Discovery (scroll + load-more across all filter values) takes minutes;
# the 60s smoke budget is useless here. A timeout is a real failure for
# discovery (it must finish and print counts), unlike a smoke test.
_DISCOVERY_RUN_TIMEOUT_S = 600.0

# Exit codes: 0 success, 1 smoke test could not be fixed, 2 could not run.
EXIT_SMOKE_FAILED = 1
EXIT_COULD_NOT_RUN = 2
EXIT_SELF_CHECK_FAILED = 3
EXIT_LINT_FAILED = 4


class GenerateScriptDriver:
    """End-to-end driver: task → 3 agents → lint → emit → smoke test → discovery self-check → bounded repair."""

    def __init__(self) -> None:
        self._task_reader: TaskReader = TaskReader(DEFAULT_PROMPT)
        self._generator: ScriptGenerator = ScriptGenerator()
        self._linter: EmittedScriptLinter = EmittedScriptLinter()

    def run(self, argv: list[str]) -> int:
        """Configure logging, run the async pipeline, return the process exit code."""
        configure_logging()
        return asyncio.run(self._run_async(argv))

    async def _run_async(self, argv: list[str]) -> int:
        """Run the async pipeline: load run, generate, lint, emit, smoke test."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        # Delete the run's previous browser profile so every run starts
        # from a clean, freshly-seeded profile.
        profile_dir = run_path / "profile"
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
            logger.info("removed stale browser profile {path}", path=profile_dir)
        path_builder = ScriptPathBuilder(run_path)
        emitter = ScriptEmitter(path_builder)
        ScriptToolsCopier().copy(run_path)
        task = self._read_task(argv, run)
        prior_feedback = PriorReportReader(run_path).read()
        concurrency = render_concurrency_context(run)
        context = concurrency
        if prior_feedback:
            context = f"{prior_feedback}\n\n---\n\n{concurrency}" if concurrency else prior_feedback
        logger.info(
            "driver received task tokens={n} run={run} parallel_runners={pr}",
            n=len(task) // 4,
            run=run.name,
            pr=run.parallel_runners if run.parallel_runners is not None else 1,
        )
        try:
            return await self._generate_and_verify(task, run_path, emitter, context, concurrency)
        except Exception:
            logger.exception("step 0 generation failed")
            return EXIT_COULD_NOT_RUN

    def _log_emit_zendriver_summary(self, emit_result: EmitResult) -> None:
        """Log a summary of zendriver-specific issues found in the emitted script."""
        summary = EmittedScriptLinter.format_zendriver_summary(emit_result.lint_findings, path=emit_result.script_path)
        if summary:
            logger.warning(summary)
        else:
            logger.info(
                "no zendriver API violations in emitted script — agent appears competent with zendriver",
            )

    async def _generate_and_verify(
        self,
        task: str,
        run_path: Path,
        emitter: ScriptEmitter,
        context: str = "",
        concurrency: str = "",
    ) -> int:
        """Run the 3-agent pipeline: explore → discover → process → lint → emit → smoke → repair."""
        # 1. Explorer — produces the TaskSplit
        split, explorer_uc = await self._generator.generate_split(task, run_path, context)
        # 2. Persist task_split.json
        self._persist_split(split, run_path)
        # 3. Pre-seed discovered_links with sample URLs
        preseed_sample_links(split.sample_document_urls, str(run_path / "metadata.db"))
        # 4. Discovery Writer (Agent 2) — only when the task needs discovery
        discovery_script: GeneratedScript | None = None
        discovery_uc: GenerateDiscoveryScriptUseCase | None = None
        if split.needs_discovery:
            discovery_script, discovery_uc = await self._generator.generate_discovery(split, run_path)
        # 5. Processing Writer (Agent 3)
        processing_script, processing_uc = await self._generator.generate_processing(
            split,
            run_path,
            concurrency=concurrency,
        )
        # 6. Assemble into GeneratedScriptSet
        script_set = GeneratedScriptSet.from_scripts(discovery_script, processing_script, split)
        # 7. Lint repair — route findings by kind to the correct writer
        script_set, discovery_uc, processing_uc = await self._lint_repair_loop(
            script_set,
            discovery_uc,
            processing_uc,
        )
        # 7b. Lint gate — a script with remaining error-severity lint findings is
        # deterministically broken; refuse to emit it.
        remaining = self._error_findings_by_kind(script_set)
        if remaining:
            for kind, findings in remaining.items():
                for f in findings:
                    logger.error(
                        "lint gate: {kind} script still has rule {rule} error: {msg}",
                        kind=kind,
                        rule=f.rule,
                        msg=f.message,
                    )
            await self._generator.close_all(explorer_uc)
            return EXIT_LINT_FAILED
        # 8. Emit both scripts
        emit_results: list[EmitResult] = []
        discovery_emit = self._emit_script(task, script_set.discovery_script(), emitter, run_path, context, emit_results)
        processing_emit = self._emit_script(task, script_set.processing_script(), emitter, run_path, context, emit_results)
        assert processing_emit is not None
        # 9. Smoke test processing script
        smoke_result = await self._smoke_test_with_sidecar(processing_emit, emitter, attempt=1)
        if not smoke_result.success:
            script_set = await self._smoke_repair_loop(processing_uc, script_set, smoke_result)
            logger.info("re-emitting scripts after smoke-test repair")
            self._cleanup_emit_artifacts(emit_results)
            emit_results.clear()
            discovery_emit = self._emit_script(task, script_set.discovery_script(), emitter, run_path, context, emit_results)
            processing_emit = self._emit_script(
                task, script_set.processing_script(), emitter, run_path, context, emit_results
            )
            assert processing_emit is not None
            smoke_result = await self._smoke_test_with_sidecar(processing_emit, emitter, attempt=2)
            if not smoke_result.success:
                await self._generator.close_all(explorer_uc)
                self._cleanup_emit_artifacts(emit_results)
                return EXIT_SMOKE_FAILED
        # 9b. Processing behavioral self-check — correctness gate.
        if split.sample_document_urls:
            self_check = await processing_self_check(processing_emit.script_path, split.sample_document_urls)
            repairs = 0
            while not self_check.success and repairs < _MAX_PROCESSING_REPAIRS:
                logger.warning(
                    "processing self-check FAILED — {v} violation(s); running repair turn {r}\n{output}",
                    v=len(self_check.violations),
                    r=repairs + 1,
                    output=self_check.output,
                )
                new_proc = await self._generator.repair_processing(
                    processing_uc,
                    format_processing_self_check_repair(self_check.output, self_check.violations),
                )
                script_set = script_set.replace_processing(new_proc)
                self._cleanup_emit_artifacts(emit_results)
                emit_results.clear()
                self._emit_script(task, script_set.discovery_script(), emitter, run_path, context, emit_results)
                processing_emit = self._emit_script(
                    task, script_set.processing_script(), emitter, run_path, context, emit_results
                )
                assert processing_emit is not None
                self_check = await processing_self_check(processing_emit.script_path, split.sample_document_urls)
                repairs += 1
            logger.info(
                "processing self-check: success={ok} downloaded_rows={n} records={r} violations={v}",
                ok=self_check.success,
                n=self_check.downloaded_rows,
                r=self_check.record_count,
                v=len(self_check.violations),
            )
            if not self_check.success:
                logger.error(
                    "processing self-check FAILED after {r} repair turns — {v} violation(s) remain; refusing to deliver\n{output}",
                    r=repairs,
                    v=len(self_check.violations),
                    output=self_check.output,
                )
                await self._generator.close_all(explorer_uc)
                self._cleanup_emit_artifacts(emit_results)
                return EXIT_SELF_CHECK_FAILED
        else:
            logger.warning("processing self-check SKIPPED — no sample_document_urls from explorer")
        # 10. Discovery self-check first — its repair turn reuses the shared browser session.
        if discovery_emit is not None and discovery_uc is not None:
            await self._discovery_self_check(task, script_set, discovery_uc, emitter, run_path, context, emit_results)
        # 11. Close the shared browser session once the self-check is done.
        await self._generator.close_all(explorer_uc)
        self._cleanup_emit_artifacts(emit_results)
        return 0

    def _persist_split(self, split: TaskSplit, run_path: Path) -> None:
        """Write task_split.json to the run directory for human review."""
        path = run_path / "task_split.json"
        path.write_text(json.dumps(split.model_dump(), indent=2), encoding="utf-8")
        logger.info("persisted task_split.json to {path}", path=path)

    def _emit_script(
        self,
        task: str,
        script: GeneratedScript | None,
        emitter: ScriptEmitter,
        run_path: Path,
        context: str,
        emit_results: list[EmitResult],
    ) -> EmitResult | None:
        """Emit one script (if not None), update sidecar, log; return its EmitResult."""
        if script is None:
            return None
        emit_result = emitter.emit(task, script, run_path)
        emit_results.append(emit_result)
        emitter.update_sidecar_prior_feedback(emit_result.sidecar_path, context)
        logger.info("emitted {kind} script at {path}", kind=script.kind, path=emit_result.script_path)
        self._log_emit_zendriver_summary(emit_result)
        return emit_result

    async def _discovery_self_check(
        self,
        task: str,
        script_set: GeneratedScriptSet,
        discovery_uc: GenerateDiscoveryScriptUseCase,
        emitter: ScriptEmitter,
        run_path: Path,
        context: str,
        emit_results: list[EmitResult],
    ) -> None:
        """Run the emitted discovery script, repair on verifier failures, then run the independent audit."""
        discovery_path = emit_results[0].script_path
        scratch_db = discovery_path.parent.parent / "smoke" / "metadata.db"
        if scratch_db.exists():
            scratch_db.unlink()
        logger.info("running discovery self-check {path}", path=discovery_path)
        result = await smoke_test_script(discovery_path, timeout=_DISCOVERY_RUN_TIMEOUT_S, timeout_is_success=False)
        log_smoke_test_result(result, discovery_path, attempt=1)
        failures = self._evaluate_self_check(discovery_path, result, scratch_db)
        current_script_set = script_set
        current_disc_path = discovery_path
        current_stdout = result.output
        if failures:
            logger.warning(
                "discovery self-check FAILED — {n} issue(s); running repair turn\n{report}",
                n=len(failures),
                report="\n".join(failures),
            )
            new_discovery = await self._generator.repair_discovery(
                discovery_uc, format_discovery_repair("\n".join(failures) or result.output)
            )
            current_script_set = current_script_set.replace_discovery(new_discovery)
            self._cleanup_emit_artifacts(emit_results)
            emit_results.clear()
            new_disc_emit = self._emit_script(
                task, current_script_set.discovery_script(), emitter, run_path, context, emit_results
            )
            self._emit_script(task, current_script_set.processing_script(), emitter, run_path, context, emit_results)
            if new_disc_emit is None:
                return
            current_disc_path = new_disc_emit.script_path
            re_result = await smoke_test_script(
                current_disc_path, timeout=_DISCOVERY_RUN_TIMEOUT_S, timeout_is_success=False
            )
            log_smoke_test_result(re_result, current_disc_path, attempt=2)
            current_stdout = re_result.output
            re_failures = self._evaluate_self_check(current_disc_path, re_result, scratch_db)
            if re_failures:
                logger.error(
                    "discovery self-check STILL failing after repair — proceeding to audit\n{report}",
                    report="\n".join(re_failures),
                )
            else:
                logger.info("discovery self-check passed after repair")
        else:
            logger.info("discovery self-check passed — proceeding to independent audit")
        await self._discovery_audit_repair(
            task,
            current_script_set,
            discovery_uc,
            emitter,
            run_path,
            context,
            emit_results,
            current_disc_path,
            current_stdout,
        )

    async def _discovery_audit_repair(
        self,
        task: str,
        script_set: GeneratedScriptSet,
        discovery_uc: GenerateDiscoveryScriptUseCase,
        emitter: ScriptEmitter,
        run_path: Path,
        context: str,
        emit_results: list[EmitResult],
        discovery_path: Path,
        self_check_stdout: str,
    ) -> None:
        """Run the independent DiscoveryAuditor and repair once on discrepancies."""
        session = self._generator._session
        if session is None:
            logger.warning("discovery audit: shared browser session closed — skipping audit")
            return
        db_path = run_path / "metadata.db"
        auditor = DiscoveryAuditor(session, db_path)
        logger.info("running independent discovery audit {path}", path=discovery_path)
        outcome = await auditor.audit(discovery_path, self_check_stdout)
        if outcome.status == "skipped":
            logger.info("discovery audit skipped — {reason}", reason=outcome.reason)
            return
        if outcome.status == "passed":
            logger.info("discovery audit passed — no discrepancies")
            return
        logger.warning("discovery audit found discrepancies — running repair turn")
        new_discovery = await self._generator.repair_discovery(discovery_uc, format_discovery_repair(outcome.report))
        new_script_set = script_set.replace_discovery(new_discovery)
        self._cleanup_emit_artifacts(emit_results)
        emit_results.clear()
        new_disc_emit = self._emit_script(task, new_script_set.discovery_script(), emitter, run_path, context, emit_results)
        self._emit_script(task, new_script_set.processing_script(), emitter, run_path, context, emit_results)
        if new_disc_emit is None:
            return
        re_result = await smoke_test_script(
            new_disc_emit.script_path, timeout=_DISCOVERY_RUN_TIMEOUT_S, timeout_is_success=False
        )
        log_smoke_test_result(re_result, new_disc_emit.script_path, attempt=3)
        re_failures = self._evaluate_self_check(new_disc_emit.script_path, re_result, db_path)
        if re_failures:
            logger.error(
                "discovery self-check STILL failing after audit repair — script emitted anyway\n{report}",
                report="\n".join(re_failures),
            )
        else:
            logger.info("discovery self-check passed after audit repair")

    def _evaluate_self_check(self, discovery_path: Path, result: SmokeTestResult, scratch_db: Path) -> list[str]:
        """Return failure lines from the manifest+verifier self-check (empty = pass)."""
        manifest_result = extract_manifest_detailed(discovery_path.read_text(encoding="utf-8"))
        if manifest_result.error is not None:
            return [manifest_result.error]
        if not result.success:
            return ["discovery script crashed:\n" + result.output]
        db_rows = self._count_discovered_links(scratch_db)
        return DiscoverySelfCheckVerifier().verify(manifest_result.manifest, result.output, db_rows)

    @staticmethod
    def _count_discovered_links(db_path: Path) -> int:
        """Return ``SELECT COUNT(*) FROM discovered_links`` (0 on miss)."""
        if not db_path.exists():
            return 0
        uri = f"file:{db_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            count = conn.execute("SELECT COUNT(*) FROM discovered_links").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            return 0
        return int(count)

    @staticmethod
    def _cleanup_emit_artifacts(emit_results: list[EmitResult]) -> None:
        """Keep only the final .py of each kind; remove .raw.py, .json, and earlier .py files."""
        if not emit_results:
            return
        by_kind: dict[str, Path] = {}
        for emit_result in emit_results:
            kind = "discovery" if "__discover__" in emit_result.script_path.name else "processing"
            by_kind[kind] = emit_result.script_path
        keepers: set[Path] = set(by_kind.values())
        for emit_result in emit_results:
            for path in (emit_result.script_path, emit_result.raw_code_path, emit_result.sidecar_path):
                if path not in keepers and path.is_file():
                    path.unlink()
                    logger.debug("removed intermediate artifact {path}", path=path)

    def _log_zendriver_findings(self, findings: list[LintFinding]) -> None:
        """Log lint findings that indicate the agent misunderstands zendriver APIs."""
        summary = EmittedScriptLinter.format_zendriver_summary(findings)
        if summary:
            logger.warning(summary)
            gaps = EmittedScriptLinter.format_zendriver_gaps(findings)
            if gaps:
                logger.warning(gaps)

    async def _lint_repair_loop(
        self,
        script_set: GeneratedScriptSet,
        discovery_uc: GenerateDiscoveryScriptUseCase | None,
        processing_uc: GenerateProcessingScriptUseCase,
    ) -> tuple[GeneratedScriptSet, GenerateDiscoveryScriptUseCase | None, GenerateProcessingScriptUseCase]:
        """Run up to ``_MAX_LINT_REPAIRS`` repair turns for lint violations, routed by kind."""
        for _ in range(_MAX_LINT_REPAIRS):
            findings_by_kind = self._error_findings_by_kind(script_set)
            if not findings_by_kind:
                break
            total = sum(len(v) for v in findings_by_kind.values())
            logger.warning("lint found {n} error(s); running repair turn(s)", n=total)
            disc_findings = findings_by_kind.get("discovery", [])
            proc_findings = findings_by_kind.get("processing", [])
            if disc_findings:
                self._log_zendriver_findings(disc_findings)
            if proc_findings:
                self._log_zendriver_findings(proc_findings)
            if disc_findings and discovery_uc is not None:
                new_disc = await self._generator.repair_discovery(discovery_uc, format_lint_repair(disc_findings))
                script_set = script_set.replace_discovery(new_disc)
            if proc_findings:
                new_proc = await self._generator.repair_processing(processing_uc, format_lint_repair(proc_findings))
                script_set = script_set.replace_processing(new_proc)
        return script_set, discovery_uc, processing_uc

    async def _smoke_repair_loop(
        self,
        processing_uc: GenerateProcessingScriptUseCase,
        script_set: GeneratedScriptSet,
        smoke_result: SmokeTestResult,
    ) -> GeneratedScriptSet:
        """Run up to ``_MAX_SMOKE_REPAIRS`` repair turns for smoke failures (processing only)."""
        if smoke_result.success:
            return script_set
        logger.warning("smoke test FAILED; running repair turn")
        new_proc = await self._generator.repair_processing(processing_uc, format_smoke_repair(smoke_result.output))
        return script_set.replace_processing(new_proc)

    def _error_findings_by_kind(self, script_set: GeneratedScriptSet) -> dict[str, list[LintFinding]]:
        """Return error-severity lint findings keyed by script kind."""
        out: dict[str, list[LintFinding]] = {}
        for script in script_set.all_scripts():
            errors = [f for f in self._linter.lint(script.python_code, kind=script.kind) if f.severity == "error"]
            if errors:
                out[script.kind] = errors
        return out

    async def _smoke_test_with_sidecar(
        self, emit_result: EmitResult, emitter: ScriptEmitter, attempt: int = 1
    ) -> SmokeTestResult:
        """Run the smoke test and merge its result into the sidecar JSON."""
        result = await smoke_test_script(emit_result.script_path)
        log_smoke_test_result(result, emit_result.script_path, attempt=attempt)
        emitter.update_sidecar_smoke(emit_result.sidecar_path, result.to_payload())
        return result

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        """Read the task from argv/stdin via the injected :class:`TaskReader`."""
        return self._task_reader.read(argv, run)


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
