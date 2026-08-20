"""Per-subtask pipeline: build → lint → emit → smoke → execute → verify → repair."""

from __future__ import annotations

import hashlib
from pathlib import Path

from browser_agent.domain.subtask_record import SubtaskRecord
from browser_agent.domain.subtask_smoke_report import SubtaskSmokeReport

_MAX_SUBTASK_ATTEMPTS = 3
_MAX_LINT_REPAIRS_PER_ATTEMPT = 1
_MAX_SMOKE_REPAIRS_PER_ATTEMPT = 1
_MAX_VERIFY_REPAIRS_PER_ATTEMPT = 1
_SMOKE_TIMEOUT_S = 60.0
_DISCOVERY_RUN_TIMEOUT_S = 600.0


class SubtaskPipeline:
    """Build, lint, emit, smoke-test, execute, and verify one subtask."""

    def __init__(self, flow_paths, emitter, linter, state_store, executor, verifier):
        self._flow_paths = flow_paths
        self._emitter = emitter
        self._linter = linter
        self._state_store = state_store
        self._executor = executor
        self._verifier = verifier
        self._run_path = flow_paths._root

    async def run(self, subtask, state, context: str) -> SubtaskRecord:

        record = self._find_or_create_record(state, subtask.subtask_id, state.plan_counter)
        record.status = "building"

        profile_dir = self._run_path / "profile_builder"

        for attempt in range(_MAX_SUBTASK_ATTEMPTS):
            record.attempts = attempt + 1
            # ``ZendriverBrowserSession.start`` resets (wipes + re-seeds) the
            # profile dir on every launch, so no upfront cleanup is needed.
            bsession, builder = await self._build_session(profile_dir, subtask.subtask_id)
            result = await self._attempt(subtask, context, record, bsession, builder, attempt)
            await bsession.close()

            if result == "succeeded":
                record.status = "succeeded"
                return record
            if result == "repair_noop":
                record.status = "repair_noop"
                return record

        record.status = record.status if record.status != "building" else "execution_failed"
        return record

    async def _attempt(self, subtask, context, record, bsession, builder, attempt):
        script = None
        for _ in range(_MAX_LINT_REPAIRS_PER_ATTEMPT + 1):
            script, record = await self._build_and_lint(subtask, context, record, builder)
            if script is None:
                continue
            if record.status == "ok":
                break
        if script is None:
            return record.status

        emit_result, record = self._emit(subtask, script, record, state=None)
        if record.status == "repair_noop":
            return "repair_noop"

        for _ in range(_MAX_SMOKE_REPAIRS_PER_ATTEMPT + 1):
            smoke, record = await self._smoke(subtask, record, emit_result)
            if smoke and smoke.smoke.success and not smoke.discovery_self_check_failures:
                if smoke.self_check is None or smoke.self_check.success:
                    break
            if record.status == "repair_noop":
                return "repair_noop"
            if smoke and not smoke.smoke.success:
                script = await builder.repair(self._format_smoke_repair(subtask.kind, smoke))
                emit_result, record = self._emit(subtask, script, record, state=None)
                if record.status == "repair_noop":
                    return "repair_noop"
            else:
                break

        exec_report = await self._executor.run(subtask.subtask_id, emit_result.script_path)
        try:
            exec_report.script_path = str(Path(exec_report.script_path).relative_to(self._run_path))
        except ValueError:
            exec_report.script_path = str(Path(exec_report.script_path))
        self._state_store.write_report(subtask.subtask_id, "execution_report", exec_report)
        if exec_report.exit_code != 0:
            from browser_agent.use_cases.script_repair_prompt import format_execution_repair

            record.status = "execution_failed"
            context = format_execution_repair(exec_report.output_tail)
            return record.status

        for _ in range(_MAX_VERIFY_REPAIRS_PER_ATTEMPT + 1):
            vreport = await self._verifier.verify(subtask, self._state_store)
            if self._verifier.passed(vreport):
                return "succeeded"
            if _ >= _MAX_VERIFY_REPAIRS_PER_ATTEMPT:
                record.status = "verification_failed"
                return record.status
            from browser_agent.use_cases.script_repair_prompt import format_verification_repair

            script = await builder.repair(format_verification_repair(vreport))
            emit_result, record = self._emit(subtask, script, record, state=None)
            if record.status == "repair_noop":
                return "repair_noop"
            smoke, record = await self._smoke(subtask, record, emit_result)
            exec_report = await self._executor.run(subtask.subtask_id, emit_result.script_path)
            try:
                exec_report.script_path = str(Path(exec_report.script_path).relative_to(self._run_path))
            except ValueError:
                exec_report.script_path = str(Path(exec_report.script_path))

        record.status = "verification_failed"
        return record.status

    async def _build_and_lint(self, subtask, context, record, builder):

        script = await builder.execute(subtask, context)
        findings = [f for f in self._linter.lint(script.python_code, kind=subtask.kind) if f.severity == "error"]
        if findings:
            return None, record
        record.status = "ok"
        return script, record

    def _emit(self, subtask, script, record, state):
        emit_result = self._emitter.emit(
            state.plan.task_summary if state else subtask.description,
            script,
            self._run_path,
        )
        try:
            record.script_path = str(emit_result.script_path.relative_to(self._run_path))
        except ValueError:
            record.script_path = str(emit_result.script_path)
        self._state_store.write_report(subtask.subtask_id, "subtask", subtask)

        code = emit_result.script_path.read_text(encoding="utf-8")
        new_hash = hashlib.md5(code.encode()).hexdigest()
        if record.script_hash and record.script_hash == new_hash:
            record.status = "repair_noop"
            return emit_result, record
        record.script_hash = new_hash
        return emit_result, record

    async def _smoke(self, subtask, record, emit_result):
        from browser_agent.drivers.generation.script_smoke_tester import (
            smoke_test_script,
            processing_self_check,
        )
        from browser_agent.use_cases.discovery_manifest_parser import extract_manifest_detailed
        from browser_agent.use_cases.discovery_self_check_verifier import DiscoverySelfCheckVerifier

        script_path = emit_result.script_path
        result = await smoke_test_script(script_path, timeout=_SMOKE_TIMEOUT_S)

        self_check = None
        disc_failures: list[str] = []
        if subtask.kind == "processing" and subtask.sample_document_urls:
            self_check = await processing_self_check(script_path, subtask.sample_document_urls)
        elif subtask.kind == "discovery":
            db_path = script_path.parent.parent / "smoke" / "metadata.db"
            if db_path.exists():
                db_path.unlink()
            disc_result = await smoke_test_script(
                script_path,
                timeout=_DISCOVERY_RUN_TIMEOUT_S,
                timeout_is_success=False,
            )
            manifest_result = extract_manifest_detailed(script_path.read_text(encoding="utf-8"))
            if manifest_result.error:
                disc_failures = [manifest_result.error]
            elif disc_result.success and manifest_result.manifest is not None:
                db_rows = self._count_discovered_links(db_path)
                disc_failures = DiscoverySelfCheckVerifier().verify(
                    manifest_result.manifest,
                    disc_result.output,
                    db_rows,
                )
            else:
                disc_failures = ["discovery script crashed:\n" + disc_result.output]

        smoke_report = SubtaskSmokeReport(
            smoke=result,
            self_check=self_check,
            discovery_self_check_failures=disc_failures,
        )
        self._state_store.write_report(subtask.subtask_id, "smoke_report", smoke_report)
        return smoke_report, record

    @staticmethod
    def _count_discovered_links(db_path: Path) -> int:
        import sqlite3

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

    async def _build_session(self, profile_dir, task_slug):
        from browser_agent.adapters.browser.zendriver_browser_session import ZendriverBrowserSession
        from browser_agent.adapters.execution.in_process_script_runner_adapter import (
            InProcessScriptRunnerAdapter,
        )
        from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
            CurlCffiPdfDownloaderAdapter,
        )
        from browser_agent.adapters.llm.opencode_zen_adapter import OpenCodeZenAdapter
        from browser_agent.use_cases.script_builder_use_case import ScriptBuilderUseCase
        from browser_agent.use_cases.agent_deps import AgentDeps
        from browser_agent.configuration import ZENDRIVER_HEADLESS

        session = ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=profile_dir,
        )
        await session.start()
        deps = AgentDeps(
            llm=OpenCodeZenAdapter(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=self._run_path / "metadata.db",
                task_slug=task_slug,
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(),
        )
        return session, ScriptBuilderUseCase(deps)

    @staticmethod
    def _find_or_create_record(state, subtask_id, plan_counter):
        for r in state.records:
            if r.subtask_id == subtask_id:
                return r
        record = SubtaskRecord(subtask_id=subtask_id, plan_index=plan_counter)
        state.records.append(record)
        return record

    def _format_smoke_repair(self, kind, smoke_report):
        from browser_agent.use_cases.script_repair_prompt import (
            format_smoke_repair,
            format_processing_self_check_repair,
            format_discovery_repair,
        )

        if smoke_report.discovery_self_check_failures:
            return format_discovery_repair("\n".join(smoke_report.discovery_self_check_failures))
        if kind == "processing" and smoke_report.self_check and not smoke_report.self_check.success:
            return format_processing_self_check_repair(
                smoke_report.self_check.output,
                smoke_report.self_check.violations,
            )
        return format_smoke_repair(smoke_report.smoke.output)
