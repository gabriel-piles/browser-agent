"""Command-line entry point for the Zendriver script generation service.

Reads a task from argv (or the bundled default), wires the
:OllamaAdapter and :ZendriverWebInspectorAdapter into an
:AgentDeps and runs the use case. The generated :GeneratedScript
is printed as JSON and the executable source is written to
``data/scripts/<slug>.py`` for the operator to launch.

Usage:
    python -m browser_agent.drivers.generate_script "<task>"
    python -m browser_agent.drivers.generate_script --stdin < task.txt
"""

from __future__ import annotations

import asyncio
import json
import sys
import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from browser_agent.adapters.browser.zendriver_web_inspector_adapter import (
    ZendriverWebInspectorAdapter,
)
from browser_agent.adapters.execution.subprocess_script_runner_adapter import (
    SubprocessScriptRunnerAdapter,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.configuration import SCRIPTS_PATH, ZENDRIVER_HEADLESS, PROJECT_ROOT
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.generate_zendriver_script_use_case import (
    GenerateZendriverScriptUseCase,
)

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_TASK = """
I want all the links like https://*/vid/45454 from this page 
https://jurisprudencia.corteidh.or.cr/search/jurisdiction:EA+content_type:79+categoriaCorte:r06r9jvba33obda+tipoDeDocumento:r06r9jye99o4szy/*
For getting all the links, it has to use the filter on the left using all the options
of the filter Estado and per page scroll down to load all the links 
from each country as the loading of the links is done dynamically when scrolling.

"""

SCRIPTS_PATH.mkdir(parents=True, exist_ok=True)


def _read_task(argv: list[str]) -> str:
    if "--stdin" in argv:
        return sys.stdin.read().strip() or DEFAULT_TASK
    if len(argv) > 1:
        return " ".join(argv[1:]).strip()
    return DEFAULT_TASK


def _script_path(task: str) -> Path:
    today = datetime.date.today().strftime("%Y_%m_%d")
    words = task.split()
    first_words = "_".join(words[:6]) if len(words) >= 6 else "_".join(words)
    slug = "".join(c if c.isalnum() else "_" for c in first_words.lower()).strip("_") or "generated"
    return SCRIPTS_PATH / f"{today}__{slug}.py"


async def _main(argv: list[str]) -> int:
    task = _read_task(argv)
    logger.info("driver received task tokens={n}", n=len(task) // 4)
    script = await _generate(task)
    _emit(task, script)
    return 0


async def _generate(task: str) -> GeneratedScript:
    deps = AgentDeps(
        llm=OllamaAdapter(),
        inspector=ZendriverWebInspectorAdapter(headless=ZENDRIVER_HEADLESS),
        script_runner=SubprocessScriptRunnerAdapter(),
    )
    return await GenerateZendriverScriptUseCase(deps).execute(CodeGenerationRequest(task=task))


def _emit(task: str, script: GeneratedScript) -> None:
    script_path = _script_path(task)
    script_path.write_text(script.python_code, encoding="utf-8")
    payload = script.model_dump()
    payload["script_path"] = str(script_path)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    configure_logging()
    raise SystemExit(asyncio.run(_main(sys.argv)))


if __name__ == "__main__":
    main()
