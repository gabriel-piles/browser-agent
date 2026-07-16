"""Top-level driver class for the Zendriver script generation service.

Reads a task from argv (or the bundled default), wires the
:class:`OllamaAdapter` and :class:`ZendriverBrowserSession` into
an :class:`AgentDeps`, runs the use case, and writes the
executable source to ``data/runs/<active_run>/scripts/<date>__<slug>.py``
for the operator to launch. The structured
:class:`GeneratedScript` (explanation, dependencies, code) is
printed as JSON alongside.

Usage:
    python -m browser_agent.drivers.step_0_generate_script "<task>"
    python -m browser_agent.drivers.step_0_generate_script --stdin < task.txt
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.domain.run_config import RunConfig
from browser_agent.drivers.generation.emitted_script_smoke_runner import EmittedScriptSmokeRunner
from browser_agent.drivers.generation.script_emitter import ScriptEmitter
from browser_agent.drivers.generation.script_generator import ScriptGenerator
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.drivers.generation.task_reader import TaskReader
from browser_agent.logging_config import configure_logging
from loguru import logger

# Hard-coded default prompt when the operator runs the driver with
# no argv and no stdin input. Pinned here per project policy: no
# CLI args in drivers, only module-level constants.
DEFAULT_PROMPT = "Visit https://quotes.toscrape.com and print every quote on the first three pages."


class GenerateScriptDriver:
    """End-to-end driver: task -> LLM agent -> emitted script -> smoke run."""

    def __init__(self) -> None:
        self._task_reader = TaskReader(DEFAULT_PROMPT)
        self._path_builder: ScriptPathBuilder | None = None
        self._generator = ScriptGenerator()
        self._emitter: ScriptEmitter | None = None
        self._smoke = EmittedScriptSmokeRunner()

    def run(self, argv: list[str]) -> int:
        """Configure logging, run the async pipeline, return the process exit code."""
        configure_logging()
        return asyncio.run(self._run_async(argv))

    async def _run_async(self, argv: list[str]) -> int:
        """Run the async pipeline: load run, build task, generate, emit, smoke."""
        run = RunsConfigLoader.load_active()
        run_path = RunsConfigLoader.load_active_path()
        self._wire_run(run_path)
        task = self._read_task(argv, run)
        logger.info(
            "driver received task tokens={n} run={run}",
            n=len(task) // 4,
            run=run.name,
        )
        script = await self._generator.generate(task, run_path)
        script_path = self._emitter.emit(task, script, run_path)
        await self._smoke.run(script_path)
        return 0

    def _wire_run(self, run_path: Path) -> None:
        """Bind the per-run path to the path builder + emitter."""
        self._path_builder = ScriptPathBuilder(run_path)
        self._emitter = ScriptEmitter(self._path_builder)

    def _read_task(self, argv: list[str], run: RunConfig) -> str:
        """Read the task from argv/stdin via the injected :class:`TaskReader`."""
        return self._task_reader.read(argv, run)


def main() -> None:
    """Module entry point: invoke the driver with the process argv."""
    raise SystemExit(GenerateScriptDriver().run(sys.argv))


if __name__ == "__main__":
    main()
