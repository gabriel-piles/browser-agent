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
import os
import subprocess
import sys
import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.in_process_script_runner_adapter import (
    InProcessScriptRunnerAdapter,
)
from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
    CurlCffiPdfDownloaderAdapter,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.emitted_clean_launch import (
    with_emitted_clean_launch,
    with_emitted_inject_profile_path,
    with_emitted_normalize_launch,
)
from browser_agent.adapters.emitted_page_wait import with_emitted_page_wait
from browser_agent.adapters.emitted_save_record import with_emitted_save_record
from browser_agent.adapters.emitted_pdf_download import with_emitted_pdf_download
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


_SMOKE_TIMEOUT_S = 20.0


async def _smoke_run(script_path: Path) -> None:
    """Run the emitted script standalone for a short window and surface errors.

    The in-process validation runner shares the agent's browser and shims
    ``import zendriver`` / ``start_browser()``, so it can hide integration
    mistakes in the final file.  This gate starts the script as the operator
    will run it (``python <file>`` in the project's virtualenv) and lets it
    execute for up to ``_SMOKE_TIMEOUT_S`` seconds.  If the script exits
    non-zero or the timeout fires, we log the captured output and raise so the
    caller knows the deliverable is broken before the operator discovers it.
    """
    venv_python = Path(sys.executable)
    logger.info(
        "smoke-running emitted script for up to {timeout}s: {path} (python={py})",
        timeout=_SMOKE_TIMEOUT_S,
        path=script_path,
        py=venv_python,
    )
    # Inherit the current environment so the venv's site-packages and any
    # project-level env vars are available to the subprocess.
    proc = await asyncio.create_subprocess_exec(
        str(venv_python),
        str(script_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(script_path.parent),
        env=os.environ.copy(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SMOKE_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        stdout, _ = await proc.communicate()
        logger.warning("smoke-run timed out after {timeout}s (script still running)", timeout=_SMOKE_TIMEOUT_S)
        return
    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    if proc.returncode != 0:
        logger.error("smoke-run failed for {path}\n{output}", path=script_path, output=output)
        raise RuntimeError(f"Emitted script failed during smoke run (exit_code={proc.returncode}): {script_path}\n{output}")
    logger.info("smoke-run succeeded for {path}", path=script_path)


async def _main(argv: list[str]) -> int:
    run = RunsConfigLoader.load_active()
    run_path = RunsConfigLoader.load_active_path()
    task = _read_task(argv, run.prompt)
    logger.info("driver received task tokens={n} run={run}", n=len(task) // 4, run=run.name)
    script = await _generate(task, run_path)
    script_path = _emit(task, script, run_path)
    await _smoke_run(script_path)
    return 0


async def _generate(task: str, run_path: Path) -> GeneratedScript:
    session = ZendriverBrowserSession(
        headless=ZENDRIVER_HEADLESS,
        user_data_dir=run_path / "profile",
    )
    deps = AgentDeps(
        llm=OllamaAdapter(),
        browser_session=session,
        script_runner=InProcessScriptRunnerAdapter(
            browser_session=session,
            metadata_db_path=run_path / "metadata.db",
            task_slug=run_path.name,
        ),
        pdf_downloader=CurlCffiPdfDownloaderAdapter(downloads_path=run_path / "downloads"),
    )
    return await GenerateZendriverScriptUseCase(deps).execute(CodeGenerationRequest(task=task))


def _emit(task: str, script: GeneratedScript, run_path: Path) -> Path:
    # Step 1 — Rewrite any ``zd.start(...)`` the LLM emitted to ``start_browser(...)``.
    # Without this the final script would pass ~22 automation-flagging Chrome args
    # that trigger anti-bot checks (the in-process validation runner hides this by
    # shimming ``zendriver.start`` to share the agent's tab).
    final_code = with_emitted_normalize_launch(script.python_code)
    # Step 2 — Inject the agent's exploration profile path so the emitted script
    # reuses the same warm profile directory (cookies, clearance tokens, local state
    # that the agent built up during live exploration).  A fresh empty profile is a
    # strong signal for Cloudflare / anti-bot; reusing the warm profile eliminates
    # the difference between the agent's browser and the emitted script's browser.
    profile_path = str((run_path / "profile").resolve())
    final_code = with_emitted_inject_profile_path(final_code, profile_path)
    # Step 3 — Prepend the vendored helper definitions (they must appear before the
    # LLM's code so forward references resolve).
    final_code = with_emitted_clean_launch(final_code)
    final_code = with_emitted_page_wait(final_code)
    final_code = with_emitted_save_record(final_code)
    final_code = with_emitted_pdf_download(final_code, script.pdf_download_strategy)
    script_path = _script_path(task, run_path)
    script_path.write_text(final_code, encoding="utf-8")
    payload = script.model_dump()
    payload["script_path"] = str(script_path)
    payload["metadata_db_path"] = str(run_path / "metadata.db")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return script_path


def main() -> None:
    configure_logging()
    raise SystemExit(asyncio.run(_main(sys.argv)))


if __name__ == "__main__":
    main()
