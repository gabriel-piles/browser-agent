"""Command-line entry point for the Zendriver script generation service.
Reads a task from argv (or the bundled default), wires the
:OllamaAdapter and :ZendriverBrowserSession into an
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

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.subprocess_script_runner_adapter import (
    SubprocessScriptRunnerAdapter,
)
from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
    CurlCffiPdfDownloaderAdapter,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.emitted_page_wait import with_emitted_page_wait
from browser_agent.adapters.emitted_save_record import with_emitted_save_record
from browser_agent.configuration import ZENDRIVER_HEADLESS, PROJECT_ROOT
from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.logging_config import configure_logging
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.generate_zendriver_script_use_case import (
    GenerateZendriverScriptUseCase,
)

load_dotenv(PROJECT_ROOT / ".env")


def _read_task(argv: list[str], run_prompt: str) -> str:
    if "--stdin" in argv:
        return sys.stdin.read().strip() or run_prompt
    if len(argv) > 1:
        return " ".join(argv[1:]).strip()
    return run_prompt


def _script_path(task: str, run_path: Path) -> Path:
    today = datetime.date.today().strftime("%Y_%m_%d")
    words = task.split()
    first_words = "_".join(words[:6]) if len(words) >= 6 else "_".join(words)
    slug = "".join(c if c.isalnum() else "_" for c in first_words.lower()).strip("_") or "generated"
    scripts_dir = run_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    return scripts_dir / f"{today}__{slug}.py"


async def _main(argv: list[str]) -> int:
    run = RunsConfigLoader.load_active()
    run_path = RunsConfigLoader.load_active_path()
    task = _read_task(argv, run.prompt)
    logger.info("driver received task tokens={n} run={run}", n=len(task) // 4, run=run.name)
    script = await _generate(task, run_path)
    _emit(task, script, run_path)
    return 0


async def _generate(task: str, run_path: Path) -> GeneratedScript:
    deps = AgentDeps(
        llm=OllamaAdapter(),
        browser_session=ZendriverBrowserSession(headless=ZENDRIVER_HEADLESS),
        script_runner=SubprocessScriptRunnerAdapter(),
        pdf_downloader=CurlCffiPdfDownloaderAdapter(downloads_path=run_path / "downloads"),
    )
    return await GenerateZendriverScriptUseCase(deps).execute(CodeGenerationRequest(task=task))


def _emit(task: str, script: GeneratedScript, run_path: Path) -> None:
    script_path = _script_path(task, run_path)
    final_code = with_emitted_page_wait(script.python_code)
    final_code = with_emitted_save_record(final_code)
    script_path.write_text(final_code, encoding="utf-8")
    payload = script.model_dump()
    payload["script_path"] = str(script_path)
    payload["metadata_db_path"] = str(run_path / "metadata.db")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> None:
    configure_logging()
    raise SystemExit(asyncio.run(_main(sys.argv)))


if __name__ == "__main__":
    main()
