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
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.run_config import RunConfig
from browser_agent.drivers.generation.script_emitter import ScriptEmitter
from browser_agent.drivers.generation.script_generator import ScriptGenerator
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier
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
    format_lint_repair,
    format_smoke_repair,
)

# Hard-coded default prompt when the operator runs the driver with
# no argv and no stdin input. Pinned here per project policy: no
# CLI args in drivers, only module-level constants.
DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."

_MAX_LINT_REPAIRS = 1
_MAX_SMOKE_REPAIRS = 1

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
        f"single-tab; only the per-document processing fans out, gated by an "
        f"asyncio.Semaphore({pr}). Open {pr} tabs with "
        "`tab = await browser.get(url, new_tab=True)` after start_browser and call "
        "`await prepare_page_wait(tab)` on EACH tab before its first navigation. "
        "Pass each task its OWN tab to download_pdf_curl_cffi / save_page_html so "
        "cookies are not shared across concurrent sessions."
    )


class GenerateScriptDriver:
    """End-to-end driver: task -> LLM agent -> lint -> emit -> smoke test."""

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
        context = _concurrency_context(run)
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

    async def _log_emit_zendriver_summary(self, emit_result: EmitResult) -> None:
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
        """Generate, lint-repair, emit, smoke-repair, return exit code."""
        script, use_case = await self._generator.generate(task, run_path, context)
        script = await self._lint_repair_loop(use_case, script)
        emit_results: list[EmitResult] = []
        emit_result = emitter.emit(task, script, run_path)
        emit_results.append(emit_result)
        logger.info("emitted script at {path}", path=emit_result.script_path)
        await self._log_emit_zendriver_summary(emit_result)
        smoke_result = await self._smoke_test_with_sidecar(emit_result, emitter)
        if smoke_result.success:
            await self._generator.close(use_case)
            self._cleanup_emit_artifacts(emit_results)
            return 0
        script = await self._smoke_repair_loop(use_case, script, smoke_result)
        emit_result = emitter.emit(task, script, run_path)
        emit_results.append(emit_result)
        smoke_result = await self._smoke_test_with_sidecar(emit_result, emitter)
        await self._generator.close(use_case)
        self._cleanup_emit_artifacts(emit_results)
        return 0 if smoke_result.success else EXIT_SMOKE_FAILED

    @staticmethod
    def _cleanup_emit_artifacts(emit_results: list[EmitResult]) -> None:
        """Keep only the final .py; remove all .raw.py, .json, and earlier .py files."""
        if not emit_results:
            return
        keeper = emit_results[-1].script_path
        for emit_result in emit_results:
            for path in (emit_result.script_path, emit_result.raw_code_path, emit_result.sidecar_path):
                if path != keeper and path.is_file():
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
        script: GeneratedScript,
    ) -> GeneratedScript:
        """Run up to ``_MAX_LINT_REPAIRS`` repair turns for lint violations."""
        for _ in range(_MAX_LINT_REPAIRS):
            findings = self._error_findings(script)
            if not findings:
                break
            logger.warning("lint found {n} error(s); running repair turn", n=len(findings))
            self._log_zendriver_findings(findings)
            script = await self._generator.repair(use_case, format_lint_repair(findings))
        return script

    async def _smoke_repair_loop(
        self,
        use_case: GenerateZendriverScriptUseCase,
        script: GeneratedScript,
        smoke_result: SmokeTestResult,
    ) -> GeneratedScript:
        """Run up to ``_MAX_SMOKE_REPAIRS`` repair turns for smoke failures."""
        if smoke_result.success:
            return script
        logger.warning("smoke test FAILED; running repair turn")
        _log_zendriver_errors_in_smoke_output(smoke_result.output, "smoke test")
        return await self._generator.repair(use_case, format_smoke_repair(smoke_result.output))

    def _error_findings(self, script: GeneratedScript) -> list[LintFinding]:
        """Return error-severity lint findings for ``script``."""
        return [f for f in self._linter.lint(script.python_code) if f.severity == "error"]

    async def _smoke_test_with_sidecar(self, emit_result: EmitResult, emitter: ScriptEmitter) -> SmokeTestResult:
        """Run the smoke test and merge its result into the sidecar JSON."""
        result = await smoke_test_script(emit_result.script_path)
        log_smoke_test_result(result, emit_result.script_path)
        emitter.update_sidecar_smoke(emit_result.sidecar_path, _smoke_payload(result))
        return result

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        """Read the task from argv/stdin via the injected :class:`TaskReader`."""
        return self._task_reader.read(argv, run)


# Common zendriver error patterns found in validation/smoke output that
# indicate the agent does not understand zendriver's API surface.
_ZD_RUNTIME_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    # tab.evaluate calling convention
    (
        "tab.evaluate",
        "evaluate() missing 1 required positional argument",
        "tab.evaluate — called without expression argument",
    ),
    (
        "TypeError: object NoneType can't be used in 'await' expression",
        "save_record is synchronous",
        "save_record — awaited synchronous helper (TypeError)",
    ),
    (
        "AttributeError: module 'zendriver' has no attribute 'start'",
        "zd.start not found",
        "zendriver.start — no such function in the module",
    ),
    (
        "TypeError: object NoneType can't be used in 'await' expression",
        "NoneType awaited",
        "save_record — awaited None (sync helper returns None)",
    ),
    (
        "TypeError: 'NoneType' object is not callable",
        "NoneType called",
        "zendriver object was None — likely wrong browser startup",
    ),
    (
        "TimeoutError: wait_for_anchors timed out after",
        "wait_for_anchors timeout",
        "wait_for_anchors — zero matches or selector wrong",
    ),
    (
        "ModuleNotFoundError: No module named 'playwright'",
        "playwright import",
        "agent imports playwright instead of using zendriver CDP API",
    ),
    ("KeyError: 'file_size'", "file_size key", "result dict has no file_size key; use 'size'"),
    (
        "zendriver.core.connection.ProtocolException",
        "ProtocolException",
        "zendriver CDP protocol error — likely bad evaluate() call",
    ),
    (
        "zendriver.core.elements.ElementNotFound",
        "ElementNotFound",
        "zendriver element not found — selector wrong or page not ready",
    ),
    # Generic Python errors indicating the agent's script is structurally broken
    ("NameError: name '", "NameError", "undefined variable — agent used wrong API name"),
    ("SyntaxError: invalid syntax", "SyntaxError", "syntax error — agent emitted malformed Python"),
    ("ImportError: cannot import name '", "ImportError", "import error — agent imports wrong module/name"),
    ("ModuleNotFoundError: No module named '", "ModuleNotFoundError", "missing module — agent imports non-existent package"),
    ("AttributeError: '", "AttributeError", "attribute error — agent called wrong method/property"),
    ("TypeError: ", "TypeError", "type error — agent passed wrong argument type"),
    (
        "asyncio.run() cannot be called from a running event loop",
        "asyncio.run error",
        "agent used asyncio.run() inside running loop",
    ),
]


async def _log_zendriver_errors_in_smoke_output(output: str, context: str) -> None:
    """Scan ``output`` for patterns indicating zendriver API misuse and log them.

    ``context`` is a label (``"smoke test"`` or ``"validation"``) used in the
    log message so the operator can trace the error to the right phase.
    """
    found: list[str] = []
    for pattern, label, description in _ZD_RUNTIME_ERROR_PATTERNS:
        if pattern in output:
            found.append(description)
            logger.warning(
                "[SMOKE ZD-ERROR] {context} — {label}: {description}",
                context=context,
                label=label,
                description=description,
            )
    if found:
        logger.warning(
            "zendriver runtime errors in {context}: {count} issue(s) — {gaps}",
            context=context,
            count=len(found),
            gaps="; ".join(found),
        )


def _smoke_payload(result: SmokeTestResult) -> dict[str, object]:
    """Convert a :class:`SmokeTestResult` to a JSON-serializable dict."""
    return {"success": result.success, "timed_out": result.timed_out, "output": result.output}


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
