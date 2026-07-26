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
        task = self._read_task(argv, run)
        logger.info("driver received task tokens={n} run={run}", n=len(task) // 4, run=run.name)
        try:
            return await self._generate_and_verify(task, run_path, emitter)
        except Exception:
            logger.exception("step 0 generation failed")
            return EXIT_COULD_NOT_RUN

    async def _generate_and_verify(self, task: str, run_path: Path, emitter: ScriptEmitter) -> int:
        """Generate, lint-repair, emit, smoke-repair, return exit code."""
        script, use_case = await self._generator.generate(task, run_path)
        script = await self._lint_repair_loop(use_case, script)
        emit_result = emitter.emit(task, script, run_path)
        logger.info("emitted script at {path}", path=emit_result.script_path)
        smoke_result = await self._smoke_test_with_sidecar(emit_result, emitter)
        if smoke_result.success:
            await self._generator.close(use_case)
            return 0
        script = await self._smoke_repair_loop(use_case, script, smoke_result)
        emit_result = emitter.emit(task, script, run_path)
        smoke_result = await self._smoke_test_with_sidecar(emit_result, emitter)
        await self._generator.close(use_case)
        return 0 if smoke_result.success else EXIT_SMOKE_FAILED

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


def _smoke_payload(result: SmokeTestResult) -> dict[str, object]:
    """Convert a :class:`SmokeTestResult` to a JSON-serializable dict."""
    return {"success": result.success, "timed_out": result.timed_out, "output": result.output}


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
