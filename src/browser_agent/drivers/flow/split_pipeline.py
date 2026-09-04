"""Per-split pipeline: explore → write → lint → emit → smoke → execute → verify → decide.

The deterministic engine that drives ONE split folder end-to-end. No
LLM orchestrator: the circuit breakers are integer caps and the only
decider after verification is the verify agent's own ``decision``
(rewrite_script / add_extra_script / re_execute / accept), applied
mechanically.
"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.clean_browser_launcher import delete_profile_dir, kill_chromium_under
from browser_agent.domain.flow_script_record import FlowScriptRecord, ScriptStatus
from browser_agent.domain.flow_subtask_spec import FlowSubtaskSpec
from browser_agent.domain.flow_verification_report import FlowVerificationReport
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.script_execution_report import ScriptExecutionReport
from browser_agent.domain.split_run_state import SplitRunState
from browser_agent.drivers.flow.flow_script_emitter import FlowScriptEmitter
from browser_agent.drivers.flow.flow_script_executor import FlowScriptExecutor
from browser_agent.drivers.flow.split_flow_paths import SplitFlowPaths
from browser_agent.use_cases.flow_verifier_use_case import FlowVerifierUseCase
from browser_agent.use_cases.script_repair_prompt import (
    format_execution_repair,
    format_lint_repair,
    format_smoke_repair,
    format_verification_repair,
)
from browser_agent.use_cases.split_state_store import SplitStateStore

_MAX_BUILD_ATTEMPTS = 3
_MAX_POST_EMIT_REPAIRS = 3
_MAX_EMITS_PER_SCRIPT = 8
_MAX_EXTRA_SCRIPTS = 3
_MAX_RE_EXECUTES = 1
_MAX_TIMEOUTS = 2
_SMOKE_TIMEOUT_S = 60.0
_AUTO_ACCEPT_MISSING_THRESHOLD = 3
_SPEC_TRUST_BLOCK = (
    "## SPEC TRUST — the spec's verified_selectors, row_selector, and sample URLs were verified "
    "live by the explorer minutes ago. Do NOT re-verify them page by page; explore only mechanics "
    "the spec leaves uncertain."
)
_SHARED_OUTPUT_BLOCK = (
    "## OUTPUT PATH — split-flow scripts live in <run>/flow/<split>/scripts/. Downloads and "
    "metadata are SHARED at the run root, FOUR levels up (scripts → split → flow → run root). "
    'Compute out_dir as Path(__file__).resolve().parent.parent.parent.parent / "downloads" '
    "(never two or three .parent levels — those land in the split folder or flow/, not "
    "the shared store the verifier reads)."
)
_REPAIR_PRESERVE_MARKERS_BLOCK = (
    "## REPAIR SCOPE — targeted retry, not full re-walk\n"
    "This is a repair of an already-working script. PRESERVE the existing "
    "session completion markers and skip-existing logic: do NOT clear markers "
    "or force a full re-walk of sessions whose parse logic is unchanged. Only "
    "re-walk a session when its parse logic actually changed. Prefer the "
    "targeted retry phase (load_failed_downloads) to re-attempt the specific "
    "missing/failed rows; already-complete records must be skipped, not "
    "re-downloaded. Never narrow the script's DEFAULT run scope to a repair "
    "subset (e.g. a backfill session list). The default run must always walk "
    "the FULL declared range with skip-existing idempotency; a bounded re-run "
    "belongs behind an env-var override (e.g. VAL_SESSIONS), never the default."
)


def _phase(split: str, label: str) -> None:
    """Log a pipeline phase transition so slow/hung phases are visible."""
    logger.info("split {id}: {label}", id=split, label=label)


def _original_task_block(task: str) -> str:
    """Render the overall goal as a context block shown before a split's own prompt."""
    return f"## ORIGINAL TASK (the overall goal this split is part of)\n{task}" if task else ""


def _timeout_note(exec_report) -> str:
    """Partial-progress prefix for verify after a timed-out execution."""
    return f"TIMED OUT after {exec_report.duration_s:.0f}s — coverage below is partial progress, not a completed run.\n"


def _timeout_repair_feedback(base: str, exec_report, report) -> str:
    """Repair prompt for timeouts: force speed/scope fix, not re-emit."""
    return f"{base}\ntimed_out=True missing={report.missing_count} duration={exec_report.duration_s:.0f}s — shrink scope or speed up instead of re-emitting the same run."


class SplitPipeline:
    """Build, verify, and decide one split's scripts."""

    def __init__(
        self,
        paths: SplitFlowPaths,
        state_store: SplitStateStore,
        emitter: FlowScriptEmitter,
        verifier: FlowVerifierUseCase,
        run_path: Path,
        prior_context: str,
        original_task: str = "",
        concurrency_directive: str = "",
    ) -> None:
        self._paths: SplitFlowPaths = paths
        self._state_store: SplitStateStore = state_store
        self._emitter: FlowScriptEmitter = emitter
        self._verifier: FlowVerifierUseCase = verifier
        self._run_path: Path = run_path
        self._prior_context: str = prior_context
        self._original_task: str = original_task
        self._concurrency_directive: str = concurrency_directive

    async def run(self, state: SplitRunState, split_prompt: str) -> SplitRunState:
        """Drive one split to a terminal outcome; state is persisted each phase."""
        if state.finished:
            _phase(state.split_name, "already finished — skipping")
            return state
        spec = await self._explore(state, split_prompt)
        if spec is None or not split_prompt:
            state.status = "exploration_failed"
            state.finished = True
            state.finished_at = _now()
            self._state_store.save(state)
            return state
        record = await self._build_and_verify_primary(state, spec)
        state = await self._apply_verify_decisions(state, spec, record)
        state.finished = True
        state.finished_at = _now()
        self._state_store.save(state)
        _phase(state.split_name, f"finished status={state.status}")
        return state

    async def _explore(self, state: SplitRunState, split_prompt: str) -> FlowSubtaskSpec | None:
        """Run (or reuse) the explorer and persist the spec into the state."""
        if state.spec:
            _phase(state.split_name, "spec exists — skipping exploration")
            return FlowSubtaskSpec.model_validate(state.spec)
        _phase(state.split_name, "exploring")
        spec = await self._run_explorer(split_prompt)
        if spec is None:
            return None
        state.spec = spec.model_dump(mode="json")
        self._state_store.save(state)
        return spec

    def _builder_context(self) -> str:
        """Context handed to the explorer: original task + prior split's spec+script."""
        blocks = [b for b in (_original_task_block(self._original_task), self._prior_context) if b]
        return "\n\n---\n\n".join(blocks)

    async def _run_explorer(self, split_prompt: str) -> FlowSubtaskSpec | None:
        """One explorer agent pass over the split's pages."""
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
            CurlCffiPdfDownloaderAdapter,
        )
        from browser_agent.adapters.execution.in_process_script_runner_adapter import (
            InProcessScriptRunnerAdapter,
        )
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.use_cases.agent_deps import AgentDeps
        from browser_agent.use_cases.flow_explorer_use_case import FlowExplorerUseCase

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=self._paths.profile_dir("explorer"),
        )
        deps = AgentDeps(
            llm=build_llm(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=self._run_path / "metadata.db",
                task_slug="exploration",
                namespace_file=self._paths.scripts_dir() / "validation.py",
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(self._run_path / "downloads"),
        )
        use_case = FlowExplorerUseCase(deps)
        try:
            return await use_case.execute(split_prompt, context=self._builder_context())
        except Exception:
            logger.exception("split explorer failed")
            return None
        finally:
            await use_case.close()
            delete_profile_dir(self._paths.profile_dir("explorer"))

    async def _build_and_verify_primary(self, state: SplitRunState, spec: FlowSubtaskSpec) -> FlowScriptRecord:
        """Build, lint, smoke, execute, and verify the primary script."""
        record = self._record_for(state, 0)
        record.status = "building"
        feedback = ""
        for attempt in range(_MAX_BUILD_ATTEMPTS):
            state.attempts = attempt + 1
            if record.emits >= _MAX_EMITS_PER_SCRIPT:
                record.status = "emit_budget_exhausted"
                logger.error(
                    "split {id}: emit budget ({n}) exhausted",
                    id=state.split_name,
                    n=_MAX_EMITS_PER_SCRIPT,
                )
                self._state_store.save(state)
                return record
            _phase(state.split_name, "building")
            outcome, feedback = await self._attempt(state, spec, record, attempt, feedback=feedback)
            self._state_store.save(state)
            if outcome in ("succeeded", "repair_noop", "accepted_gap"):
                return record
            if outcome == "verification_failed" and attempt + 1 >= _MAX_BUILD_ATTEMPTS:
                break
        if record.status == "building":
            record.status = "execution_failed"
        self._state_store.save(state)
        return record

    async def _attempt(
        self,
        state: SplitRunState,
        spec: FlowSubtaskSpec,
        record: FlowScriptRecord,
        attempt: int,
        feedback: str,
    ) -> tuple[str, str]:
        """One build attempt: write → lint → emit → smoke → execute → verify.

        Returns ``(outcome, last_feedback)``. ``outcome`` is the record's
        terminal status for this attempt (or ``building`` when the loop
        should continue).
        """
        _phase(state.split_name, "writing")
        context = self._writer_context(feedback, record)
        script = await self._write(spec, context)
        _phase(state.split_name, "lint repair")
        script, findings = await self._lint_repair(spec, script, record)
        if findings:
            record.status = "lint_failed"
            return "lint_failed", format_lint_repair(findings)
        _phase(state.split_name, "emitting")
        emit_result = self._emit(state, spec, script, record)
        if emit_result is None:
            return record.status, ""
        _phase(state.split_name, "post-emit chain")
        return await self._post_emit(state, spec, record, emit_result)

    def _prior_script_block(self, record: FlowScriptRecord) -> str:
        """Render the prior emitted script as context; "" when absent."""
        path = self._existing_script_path(record)
        if path is None:
            return ""
        return (
            "## Prior emitted script (from the previous attempt)\n"
            "Edit this incrementally instead of re-exploring the site. Keep its verified "
            "selectors and page mechanics; change only what the repair finding describes.\n"
            f"Path: {path}\n```python\n{path.read_text(encoding='utf-8', errors='replace')}\n```"
        )

    def _writer_context(self, feedback: str, record: FlowScriptRecord) -> str:
        """Seed the writer with the prior emitted script and repair feedback."""
        parts = [
            b for b in (self._concurrency_directive, _original_task_block(self._original_task), self._prior_context) if b
        ]
        prior = self._prior_script_block(record)
        if prior:
            parts.append(prior)
        parts.extend((_SPEC_TRUST_BLOCK, _SHARED_OUTPUT_BLOCK))
        if feedback:
            parts.append(_REPAIR_PRESERVE_MARKERS_BLOCK)
            parts.append(feedback)
        return "\n\n".join(parts)

    async def _write(self, spec: FlowSubtaskSpec, context: str) -> GeneratedScript:
        """One writer agent turn (fresh session per turn)."""
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
            CurlCffiPdfDownloaderAdapter,
        )
        from browser_agent.adapters.execution.in_process_script_runner_adapter import (
            InProcessScriptRunnerAdapter,
        )
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm
        from browser_agent.configuration import ZENDRIVER_HEADLESS
        from browser_agent.use_cases.agent_deps import AgentDeps
        from browser_agent.use_cases.flow_writer_use_case import FlowWriterUseCase

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=self._paths.profile_dir("writer"),
        )
        deps = AgentDeps(
            llm=build_llm(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=self._paths.scratch_dir() / "validation_metadata.db",
                task_slug=f"validation_{spec.subtask_id}",
                namespace_file=self._paths.scripts_dir() / "validation.py",
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(self._paths.scratch_dir() / "validation_downloads"),
        )
        writer = FlowWriterUseCase(deps)
        try:
            return await writer.execute_spec(spec, context)
        finally:
            await session.close()
            delete_profile_dir(self._paths.profile_dir("writer"))

    async def _lint_repair(
        self, spec: FlowSubtaskSpec, script: GeneratedScript, record: FlowScriptRecord
    ) -> tuple[GeneratedScript, list[LintFinding]]:
        """One lint-repair writer turn; returns (script, remaining_error_findings)."""
        findings = self._emitter.lint_findings(script.python_code)
        if not findings:
            return script, []
        context = self._writer_context(format_lint_repair(findings), record)
        repaired = await self._write(spec, context)
        remaining = self._emitter.lint_findings(repaired.python_code)
        return repaired, remaining

    def _emit(self, state: SplitRunState, spec: FlowSubtaskSpec, script: GeneratedScript, record: FlowScriptRecord):
        """Emit one script; detect repair stagnation via md5; None when budget is out."""
        if record.emits >= _MAX_EMITS_PER_SCRIPT:
            record.status = "emit_budget_exhausted"
            self._state_store.save(state)
            return None
        record.emits += 1
        emit_result = self._emitter.emit(spec.subtask_id, script, record.script_index)
        record.script_path = str(emit_result.script_path)
        code = emit_result.script_path.read_text(encoding="utf-8")
        new_hash = hashlib.md5(code.encode()).hexdigest()
        if record.script_hash and record.script_hash == new_hash:
            record.status = "repair_noop"
            self._state_store.save(state)
            return emit_result
        record.script_hash = new_hash
        self._state_store.save(state)
        return emit_result

    async def _post_emit(
        self, state: SplitRunState, spec: FlowSubtaskSpec, record: FlowScriptRecord, emit_result
    ) -> tuple[str, str]:
        """Smoke → execute → verify with bounded repairs; returns (outcome, feedback)."""
        last_status: ScriptStatus = "verification_failed"
        for _ in range(_MAX_POST_EMIT_REPAIRS + 1):
            _phase(state.split_name, "smoke")
            smoke_failed, smoke_output = await self._smoke(emit_result.script_path)
            if smoke_failed:
                record.status = "smoke_failed"
                last_status, last_feedback = "smoke_failed", format_smoke_repair(smoke_output)
                emit_result = await self._repair_and_reemit(state, spec, record, last_feedback, last_status)
                if emit_result is None:
                    return last_status, last_feedback
                continue
            _phase(state.split_name, "executing")
            exec_report = await self._execute(spec, record, emit_result.script_path)
            if exec_report.timed_out and self._note_timeout(state, record):
                return "accepted_gap", ""
            if exec_report.exit_code != 0 and not exec_report.timed_out:
                record.status = "execution_failed"
                last_status, last_feedback = "execution_failed", format_execution_repair(exec_report.output_tail)
                emit_result = await self._repair_and_reemit(state, spec, record, last_feedback, last_status)
                if emit_result is None:
                    return last_status, last_feedback
                continue
            _phase(state.split_name, "verifying")
            previous = await self._read_last_report()
            report = await self._verify(state, spec, exec_report=exec_report, previous=previous)
            if report.decision.action == "accept" and report.missing_count == 0:
                record.status = "accepted_gap"
                return "accepted_gap", ""
            if self._verifier.passed(report):
                record.status = "succeeded"
                return "succeeded", ""
            if self._should_auto_accept(report, previous):
                record.status = "accepted_gap"
                logger.info(
                    "split {id}: missing set unchanged from prior verify (<= {n}) — auto-accepting",
                    id=state.split_name,
                    n=_AUTO_ACCEPT_MISSING_THRESHOLD,
                )
                return "accepted_gap", ""
            if report.decision.action == "re_execute":
                return await self._re_execute_and_reverify(state, spec, record, emit_result, report)
            record.status = "verification_failed"
            last_status = "verification_failed"
            last_feedback = (
                report.decision.focus if report.decision.action == "rewrite_script" else format_verification_repair(report)
            )
            if exec_report.timed_out:
                last_feedback = _timeout_repair_feedback(last_feedback, exec_report, report)
            emit_result = await self._repair_and_reemit(state, spec, record, last_feedback, last_status)
            if emit_result is None:
                return last_status, last_feedback

        return last_status, last_feedback

    async def _re_execute_and_reverify(
        self,
        state: SplitRunState,
        spec: FlowSubtaskSpec,
        record: FlowScriptRecord,
        emit_result,
        report: FlowVerificationReport,
    ) -> tuple[str, str]:
        """Re-run the already-correct script (idempotent) and re-verify once.

        The verify agent chose ``re_execute``: the gap is an interrupted or
        incomplete execution, not a code bug. Re-running the emitted script
        (skip-existing) completes the gap with no writer turn and no emit.
        Bounded to one re-execute: after the re-verify the outcome is
        terminal (succeeded or accepted_gap) regardless of the second
        report's decision, so a stubborn verifier cannot loop.
        """
        if record.re_executes >= _MAX_RE_EXECUTES:
            logger.warning("split {id}: re-execute cap ({n}) reached — accepting", id=state.split_name, n=_MAX_RE_EXECUTES)
            record.status = "accepted_gap"
            self._state_store.save(state)
            return "accepted_gap", ""
        record.re_executes += 1
        self._state_store.save(state)
        _phase(state.split_name, "re-executing")
        exec_report = await self._execute(spec, record, emit_result.script_path)
        if exec_report.exit_code != 0:
            record.status = "execution_failed"
            return "execution_failed", format_execution_repair(exec_report.output_tail)
        _phase(state.split_name, "re-verifying")
        report = await self._verify(state, spec, exec_report=exec_report, previous=report)
        if self._verifier.passed(report) or report.missing_count == 0:
            record.status = "succeeded"
            return "succeeded", ""
        record.status = "accepted_gap"
        return "accepted_gap", ""

    async def _repair_and_reemit(
        self,
        state: SplitRunState,
        spec: FlowSubtaskSpec,
        record: FlowScriptRecord,
        feedback: str,
        failed_status: ScriptStatus,
    ):
        """One repair turn + re-emit; None when the repair or budget fails."""
        try:
            prior = self._prior_script_block(record)
            context = feedback if not prior else f"{prior}\n\n{feedback}"
            script = await self._write(spec, context)
        except Exception:
            logger.exception("writer repair turn failed")
            record.status = failed_status
            self._state_store.save(state)
            return None
        emit_result = self._emit(state, spec, script, record)
        if emit_result is None:
            return None
        if record.status == "repair_noop":
            record.status = failed_status
            self._state_store.save(state)
            return None
        return emit_result

    async def _smoke(self, script_path: Path) -> tuple[bool, str]:
        """Run the smoke subprocess; returns (failed, output)."""
        from browser_agent.drivers.generation.script_smoke_tester import log_smoke_test_result, smoke_test_script

        result = await smoke_test_script(script_path, timeout=_SMOKE_TIMEOUT_S)
        log_smoke_test_result(result, script_path)
        return (not result.success), result.output

    async def _execute(self, spec: FlowSubtaskSpec, record: FlowScriptRecord, script_path: Path):
        """Run the emitted script as a subprocess against the shared stores."""
        from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier

        ScriptToolsCopier().copy(self._paths.split_dir())
        executor = FlowScriptExecutor(self._run_path, self._paths.execution_log_path(record.script_index))
        try:
            return await executor.run(spec.subtask_id, script_path)
        finally:
            kill_chromium_under(self._run_path)

    def _note_timeout(self, state: SplitRunState, record: FlowScriptRecord) -> bool:
        """Count a timeout; True when the two-timeout accept backstop hits."""
        record.timeouts += 1
        self._state_store.save(state)
        if record.timeouts < _MAX_TIMEOUTS:
            return False
        logger.warning("split {id}: timeout cap ({n}) reached — accepting", id=state.split_name, n=_MAX_TIMEOUTS)
        record.status = "accepted_gap"
        self._state_store.save(state)
        return True

    async def _verify(
        self,
        state: SplitRunState,
        spec: FlowSubtaskSpec,
        exec_report: ScriptExecutionReport | None = None,
        previous: FlowVerificationReport | None = None,
    ) -> FlowVerificationReport:
        """Verify the split's downloads so far (all its scripts together)."""
        sources = [
            Path(r.script_path).read_text(encoding="utf-8", errors="replace")
            for r in state.scripts
            if r.script_path and Path(r.script_path).is_file()
        ]
        summary = exec_report.output_tail[-2000:] if exec_report is not None else ""
        if exec_report is not None and exec_report.timed_out:
            summary = _timeout_note(exec_report) + summary
        return await self._verifier.verify(spec, sources, execution_summary=summary, previous_report=previous)

    async def _apply_verify_decisions(
        self,
        state: SplitRunState,
        spec: FlowSubtaskSpec,
        record: FlowScriptRecord,
    ) -> SplitRunState:
        """Act on the last verification's decision: rewrite, extra script, or accept."""
        if record.status == "accepted_gap":
            state.status = "accepted_gap"
            return state
        if record.status == "succeeded":
            state.status = "succeeded"
            return state
        report = await self._read_last_report()
        if report is None:
            if record.status in ("repair_noop", "emit_budget_exhausted"):
                state.status = "accepted_gap"
            else:
                state.status = record.status
            return state
        decision = report.decision
        logger.info(
            "split {id}: verify decision action={action} focus={focus}",
            id=state.split_name,
            action=decision.action,
            focus=decision.focus[:200],
        )
        if decision.action == "rewrite_script" and record.status not in ("repair_noop", "emit_budget_exhausted"):
            _phase(state.split_name, "verify-directed rewrite")
            outcome, _feedback = await self._attempt(state, spec, record, attempt=state.attempts, feedback=decision.focus)
            state.status = "succeeded" if outcome == "succeeded" else "accepted_gap"
            return state
        if decision.action == "add_extra_script":
            return await self._build_extra_script(state, spec, decision.focus)
        state.status = "accepted_gap"
        return state

    async def _build_extra_script(self, state: SplitRunState, spec: FlowSubtaskSpec, focus: str) -> SplitRunState:
        """Build one verify-requested extra script for uncovered paths."""
        extra_index = len(state.scripts)
        if extra_index > _MAX_EXTRA_SCRIPTS:
            logger.warning(
                "split {id}: extra-script cap ({n}) reached — accepting",
                id=state.split_name,
                n=_MAX_EXTRA_SCRIPTS,
            )
            state.status = "accepted_gap"
            return state
        _phase(state.split_name, f"building extra script {extra_index}")
        extra_record = FlowScriptRecord(script_index=extra_index, status="building")
        state.scripts.append(extra_record)
        self._state_store.save(state)
        extra_spec = self._extra_spec(spec, focus)
        outcome, _feedback = await self._attempt(state, extra_spec, extra_record, attempt=0, feedback=focus)
        state.status = "succeeded" if outcome == "succeeded" else "accepted_gap"
        return state

    def _extra_spec(self, spec: FlowSubtaskSpec, focus: str) -> FlowSubtaskSpec:
        """Derive the extra script's spec from the primary spec + verify focus."""
        return FlowSubtaskSpec(
            subtask_id=f"{spec.subtask_id}_extra",
            description=(
                "EXTRA SCRIPT requested by the verify agent to cover paths the primary "
                f"script did not cover.\n\nPRIMARY SCRIPT'S SCOPE (do not duplicate):\n{spec.description}\n\n"
                f"VERIFIER-DIRECTED SCOPE FOR THIS SCRIPT:\n{focus}"
            ),
            verified_selectors=spec.verified_selectors,
            field_specs=spec.field_specs,
            row_selector=spec.row_selector,
            sample_document_urls=[],
            pdf_download_strategy=spec.pdf_download_strategy,
            expected_document_count=0,
        )

    async def _read_last_report(self) -> FlowVerificationReport | None:
        """Load the last persisted flow verification report for this split."""
        path = self._paths.verification_dir() / "verification_report.json"
        if not path.is_file():
            return None
        try:
            return FlowVerificationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to load last verification report")
            return None

    def _should_auto_accept(
        self,
        report: FlowVerificationReport,
        previous: FlowVerificationReport | None,
    ) -> bool:
        """Auto-accept when the missing set is unchanged and small.

        Two consecutive verifies with an identical missing set (same
        missing-coverage paths and non-present URLs) and a count at or
        below the threshold mean the last rewrite did not move the gap —
        another build/verify cycle would only re-attempt the same dead
        paths. This is the deterministic guard against the verify agent
        repeatedly choosing ``rewrite_script`` for a stable, small gap.
        """
        if previous is None:
            return False
        current = self._gap_signature(report)
        if not current or len(current) > _AUTO_ACCEPT_MISSING_THRESHOLD:
            return False
        return current == self._gap_signature(previous)

    @staticmethod
    def _gap_signature(report: FlowVerificationReport) -> frozenset[str]:
        """Stable identity of the reported gap: missing paths + non-present URLs."""
        paths = {mc.navigation_path for mc in report.missing_coverage}
        urls = {r.url for r in report.pdf_results if r.verdict != "present"}
        return frozenset(paths | urls)

    def _existing_script_path(self, record: FlowScriptRecord) -> Path | None:
        """Resolve the record's stored script path when the file exists."""
        if not record.script_path:
            return None
        path = Path(record.script_path)
        return path if path.is_file() else None

    def _record_for(self, state: SplitRunState, script_index: int) -> FlowScriptRecord:
        """Find or create the record for one script index."""
        for record in state.scripts:
            if record.script_index == script_index:
                return record
        record = FlowScriptRecord(script_index=script_index)
        state.scripts.append(record)
        return record


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()
