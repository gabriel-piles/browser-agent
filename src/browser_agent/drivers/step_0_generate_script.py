"""Top-level driver class for the Zendriver script generation service.

Reads a task from argv (or the bundled default), wires the
:class:`OllamaAdapter` and :class:`ZendriverBrowserSession` into
an :class:`AgentDeps`, runs the use case, and writes the
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
import sys
from pathlib import Path

from loguru import logger

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.generated_script_set import GeneratedScriptSet
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.run_config import RunConfig
from browser_agent.drivers.generation.script_emitter import ScriptEmitter
from browser_agent.drivers.generation.script_generator import ScriptGenerator
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier
from browser_agent.drivers.generation.prior_report_reader import PriorReportReader
from browser_agent.drivers.generation.script_smoke_tester import (
    SmokeTestResult,
    log_smoke_test_result,
    smoke_test_script,
)
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter
from browser_agent.use_cases.generate_zendriver_script_use_case import (
    GenerateZendriverScriptUseCase,
)
from browser_agent.use_cases.script_repair_prompt import (
    format_discovery_repair,
    format_lint_repair,
    format_smoke_repair,
)

# Hard-coded default prompt when the operator runs the driver with
# no argv and no stdin input. Pinned here per project policy: no
# CLI args in drivers, only module-level constants.
DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."

_MAX_LINT_REPAIRS = 1
_MAX_SMOKE_REPAIRS = 1
# One repair cycle is the chosen bound: verification + repair +
# re-verification already adds two site re-walks; more cycles multiply
# runtime on LLM judgement that diminishingly improves.
_MAX_DISCOVERY_REPAIRS = 1

# Discovery (scroll + load-more across all filter values) takes minutes;
# the 60s smoke budget is useless here. A timeout is a real failure for
# discovery (it must finish and print counts), unlike a smoke test.
_DISCOVERY_RUN_TIMEOUT_S = 600.0

# Exit codes: 0 success, 1 smoke test could not be fixed, 2 could not run.
EXIT_SMOKE_FAILED = 1
EXIT_COULD_NOT_RUN = 2


def _concurrency_context(run: RunConfig) -> str:
    """Render the concurrency directive the agent sees, or "" for single-tab.

    When ``run.parallel_runners`` is set (>= 2), returns a directive that
    instructs the agent to fan the per-document phase out across that many
    tabs; otherwise returns "" so the classic single-tab flow is unchanged.
    """
    pr = run.parallel_runners
    if pr is None or pr <= 1:
        return ""
    return (
        "# Concurrency requirement\n"
        f"parallel_runners = {pr}\n"
        f"The script MUST process documents across {pr} browser tabs concurrently "
        f"(see the Concurrency / multi-tab section of the script rules). "
        "Discovery (filter iteration + link collection + scroll/load-more) stays "
        f"single-tab; only the per-document processing fans out across {pr} tabs via ONE worker coroutine per tab consuming "
        f"a shared asyncio.Queue (FORBIDDEN: idx % N tab assignment "
        f"behind a global asyncio.Semaphore — concurrent tab.get() on "
        f"a shared tab invalidates element handles). Open {pr} tabs with "
        "`tab = await browser.get(url, new_tab=True)` after start_browser and call "
        "`await prepare_page_wait(tab)` on EACH tab before its first navigation. "
        "Pass each task its OWN tab to download_pdf_curl_cffi / save_page_html so "
        "cookies are not shared across concurrent sessions. "
        "Foreground-gated SPAs (Aurelia/vLex/Corte IDH, React lazy mounts) "
        "render late-bound metadata ONLY in the visible tab — concurrent "
        "per-tab bring_to_front() calls steal foreground from each other and "
        "N-1 tabs' metadata never renders (gate timeout -> load_failed). "
        "Declare `gate_lock = asyncio.Lock()` before the workers and wrap the "
        "navigate + bring_to_front + metadata-gate (+ retry) block in "
        "`async with gate_lock:`; release before extraction/download so PDF "
        "I/O still parallelizes (rule 15h, lint-enforced)."
    )


class GenerateScriptDriver:
    """End-to-end driver: task -> LLM agent -> lint -> emit -> smoke test -> discovery self-check -> bounded repair."""

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
        path_builder = ScriptPathBuilder(run_path)
        emitter = ScriptEmitter(path_builder)
        ScriptToolsCopier().copy(run_path)
        task = self._read_task(argv, run)
        prior_feedback = PriorReportReader(run_path).read()
        context = _concurrency_context(run)
        if prior_feedback:
            context = f"{prior_feedback}\n\n---\n\n{context}" if context else prior_feedback
        logger.info(
            "driver received task tokens={n} run={run} parallel_runners={pr}",
            n=len(task) // 4,
            run=run.name,
            pr=run.parallel_runners if run.parallel_runners is not None else 1,
        )
        try:
            return await self._generate_and_verify(task, run_path, emitter, context)
        except Exception:
            logger.exception("step 0 generation failed")
            return EXIT_COULD_NOT_RUN

    def _log_emit_zendriver_summary(self, emit_result: EmitResult) -> None:
        """Log a summary of zendriver-specific issues found in the emitted script."""
        findings = emit_result.lint_findings
        zd_findings = EmittedScriptLinter.zendriver_findings(findings)
        if zd_findings:
            for f in zd_findings:
                loc = f" line {f.line}" if f.line is not None else ""
                concept = EmittedScriptLinter.describe_zendriver_finding(f)
                logger.warning(
                    "[EMIT ZD-ERROR] {path}: rule={rule}{loc} — {concept}: {msg}",
                    path=emit_result.script_path,
                    rule=f.rule,
                    loc=loc,
                    concept=concept,
                    msg=f.message,
                )
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
    ) -> int:
        """Generate, lint-repair, emit, smoke-repair, run discovery self-check, bounded repair."""
        script_set, use_case = await self._generator.generate(task, run_path, context)
        script_set = await self._lint_repair_loop(use_case, script_set)
        emit_results: list[EmitResult] = []
        discovery_emit = self._emit_script(task, script_set.discovery_script(), emitter, run_path, context, emit_results)
        processing_emit = self._emit_script(task, script_set.processing_script(), emitter, run_path, context, emit_results)
        assert processing_emit is not None
        smoke_result = await self._smoke_test_with_sidecar(processing_emit, emitter, attempt=1)
        if not smoke_result.success:
            script_set = await self._smoke_repair_loop(use_case, script_set, smoke_result)
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
                await self._generator.close(use_case)
                self._cleanup_emit_artifacts(emit_results)
                return EXIT_SMOKE_FAILED
        await self._generator.close(use_case)
        if discovery_emit is not None:
            await self._discovery_self_check(task, script_set, use_case, emitter, run_path, context, emit_results)
        self._cleanup_emit_artifacts(emit_results)
        return 0

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
        use_case: GenerateZendriverScriptUseCase,
        emitter: ScriptEmitter,
        run_path: Path,
        context: str,
        emit_results: list[EmitResult],
    ) -> None:
        """Run the emitted discovery script (600s) and repair once on UNDER-COLLECTED."""
        discovery_path = emit_results[0].script_path
        logger.info("running discovery self-check {path}", path=discovery_path)
        result = await smoke_test_script(discovery_path, timeout=_DISCOVERY_RUN_TIMEOUT_S, timeout_is_success=False)
        log_smoke_test_result(result, discovery_path, attempt=1)
        if result.success and "UNDER-COLLECTED" not in result.output:
            logger.info("discovery self-check passed")
            return
        paths = _under_collected_paths(result.output)
        logger.warning("discovery self-check UNDER-COLLECTS on {paths} — running repair turn", paths=paths)
        script_set = await self._generator.repair(use_case, format_discovery_repair(result.output))
        script_set = await self._lint_repair_loop(use_case, script_set)
        self._cleanup_emit_artifacts(emit_results)
        emit_results.clear()
        new_discovery = self._emit_script(task, script_set.discovery_script(), emitter, run_path, context, emit_results)
        self._emit_script(task, script_set.processing_script(), emitter, run_path, context, emit_results)
        if new_discovery is None:
            return
        re_result = await smoke_test_script(
            new_discovery.script_path, timeout=_DISCOVERY_RUN_TIMEOUT_S, timeout_is_success=False
        )
        log_smoke_test_result(re_result, new_discovery.script_path, attempt=2)
        if re_result.success and "UNDER-COLLECTED" not in re_result.output:
            logger.info("discovery self-check passed after repair")
        else:
            logger.error(
                "discovery self-check STILL under-collects after repair on {paths} — script emitted anyway",
                paths=_under_collected_paths(re_result.output),
            )

    @staticmethod
    def _cleanup_emit_artifacts(emit_results: list[EmitResult]) -> None:
        """Keep only the final .py of each kind; remove .raw.py, .json, and earlier .py files."""
        if not emit_results:
            return
        keepers: set[Path] = set()
        by_kind: dict[str, Path] = {}
        for emit_result in emit_results:
            kind = "discovery" if "__discover__" in emit_result.script_path.name else "processing"
            by_kind[kind] = emit_result.script_path
        keepers = set(by_kind.values())
        for emit_result in emit_results:
            for path in (emit_result.script_path, emit_result.raw_code_path, emit_result.sidecar_path):
                if path not in keepers and path.is_file():
                    path.unlink()
                    logger.debug("removed intermediate artifact {path}", path=path)

    def _log_zendriver_findings(self, findings: list[LintFinding]) -> None:
        """Log lint findings that indicate the agent misunderstands zendriver APIs."""
        zd_findings = EmittedScriptLinter.zendriver_findings(findings)
        for f in zd_findings:
            loc = f" line {f.line}" if f.line is not None else ""
            concept = EmittedScriptLinter.describe_zendriver_finding(f)
            logger.warning(
                "[ZD-ERROR] rule={rule}{loc} — {concept}: {msg}",
                rule=f.rule,
                loc=loc,
                concept=concept,
                msg=f.message,
            )
        if zd_findings:
            logger.warning(
                "zendriver knowledge gaps: {n} rule violation(s) — agent does not understand: {gaps}",
                n=len(zd_findings),
                gaps="; ".join(sorted({EmittedScriptLinter.describe_zendriver_finding(f) for f in zd_findings})),
            )

    async def _lint_repair_loop(
        self,
        use_case: GenerateZendriverScriptUseCase,
        script_set: GeneratedScriptSet,
    ) -> GeneratedScriptSet:
        """Run up to ``_MAX_LINT_REPAIRS`` repair turns for lint violations on all scripts."""
        for _ in range(_MAX_LINT_REPAIRS):
            findings = self._set_error_findings(script_set)
            if not findings:
                break
            logger.warning("lint found {n} error(s); running repair turn", n=len(findings))
            self._log_zendriver_findings(findings)
            script_set = await self._generator.repair(use_case, format_lint_repair(findings))
        return script_set

    async def _smoke_repair_loop(
        self,
        use_case: GenerateZendriverScriptUseCase,
        script_set: GeneratedScriptSet,
        smoke_result: SmokeTestResult,
    ) -> GeneratedScriptSet:
        """Run up to ``_MAX_SMOKE_REPAIRS`` repair turns for smoke failures."""
        if smoke_result.success:
            return script_set
        logger.warning("smoke test FAILED; running repair turn")
        return await self._generator.repair(use_case, format_smoke_repair(smoke_result.output))

    def _set_error_findings(self, script_set: GeneratedScriptSet) -> list[LintFinding]:
        """Return error-severity lint findings across all scripts in the set."""
        out: list[LintFinding] = []
        for script in script_set.all_scripts():
            out.extend(f for f in self._linter.lint(script.python_code, kind=script.kind) if f.severity == "error")
        return out

    async def _smoke_test_with_sidecar(
        self, emit_result: EmitResult, emitter: ScriptEmitter, attempt: int = 1
    ) -> SmokeTestResult:
        """Run the smoke test and merge its result into the sidecar JSON."""
        result = await smoke_test_script(emit_result.script_path)
        log_smoke_test_result(result, emit_result.script_path, attempt=attempt)
        emitter.update_sidecar_smoke(emit_result.sidecar_path, _smoke_payload(result))
        return result

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        """Read the task from argv/stdin via the injected :class:`TaskReader`."""
        return self._task_reader.read(argv, run)


def _smoke_payload(result: SmokeTestResult) -> dict[str, object]:
    """Convert a :class:`SmokeTestResult` to a JSON-serializable dict."""
    return {"success": result.success, "timed_out": result.timed_out, "output": result.output}


def _path_header(line: str) -> str | None:
    """Return the path from a ``--- <path> ---`` header line, or None."""
    stripped = line.strip()
    if not (stripped.startswith("---") and stripped.endswith("---")):
        return None
    return stripped[4:-4].strip() or None


def _under_collected_paths(output: str) -> list[str]:
    """Extract the paths flagged UNDER-COLLECTED from the discovery script output.

    Matches the print format the system prompt mandates: ``--- <path> ---``
    sets the current path, and any line containing ``UNDER-COLLECTED``
    attributes the gap to it.
    """
    paths: list[str] = []
    current = "<unknown>"
    for line in output.splitlines():
        header = _path_header(line)
        if header is not None:
            current = header
        elif "UNDER-COLLECTED" in line:
            paths.append(current)
    return paths


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
