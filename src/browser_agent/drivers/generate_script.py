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
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from browser_agent.adapters.browser.zendriver_web_inspector_adapter import (
    ZendriverWebInspectorAdapter,
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

DEFAULT_TASK = (
    "Visit https://quotes.toscrape.com and print the text and author of every "
    "quote on the first three pages, paginating via the 'Next' button."
)

SCRIPTS_PATH.mkdir(parents=True, exist_ok=True)


def _read_task(argv: list[str]) -> str:
    if "--stdin" in argv:
        return sys.stdin.read().strip() or DEFAULT_TASK
    if len(argv) > 1:
        return " ".join(argv[1:]).strip()
    return DEFAULT_TASK


def _script_path(task: str) -> Path:
    slug = "".join(c if c.isalnum() else "_" for c in task.lower())[:60].strip("_") or "generated"
    return SCRIPTS_PATH / f"{slug}.py"


async def _main(argv: list[str]) -> int:
    task = _read_task(argv)
    logger.info("driver received task length={n}", n=len(task))
    script = await _generate(task)
    _emit(task, script)
    return 0


async def _generate(task: str) -> GeneratedScript:
    deps = AgentDeps(
        llm=OllamaAdapter(),
        inspector=ZendriverWebInspectorAdapter(headless=ZENDRIVER_HEADLESS),
    )
    return await GenerateZendriverScriptUseCase(deps).execute(
        CodeGenerationRequest(task=task)
    )


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
