"""Run a single robustness scenario end-to-end.

Steps:
1. Start the fixture server (or reuse an already-running one).
2. Create a run config YAML in ``data/prompts/robustness_<scenario>.yaml``.
3. Set ``data/active_run.yaml`` to point at this run.
4. Invoke ``GenerateScriptDriver().run([])`` — the existing driver.
5. After the driver completes, run the emitted script from the run dir.
6. Verify the output against ``ExpectedOutput``.
7. Return a ``ScenarioResult`` (pass/fail + failure output).

Constants only — no CLI args per AGENTS.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import yaml
from loguru import logger

from browser_agent.configuration import PROMPTS_PATH, RUNS_FILE
from browser_agent.domain.robustness_scenario import RobustnessScenario
from browser_agent.domain.scenario_result import ScenarioResult
from browser_agent.drivers.step_0_run_prompt import GenerateScriptDriver

# Import verify_output from the scripts dir (added to sys.path by the caller).
sys.path.insert(0, str(Path(__file__).parent))
from verify_output import verify  # noqa: E402

EMITTED_SCRIPT_TIMEOUT_S = 120.0
RUN_PREFIX = "robustness_"
_HEADLESS_ENV = {"ZENDRIVER_HEADLESS": "true"}


def run_scenario(scenario: RobustnessScenario, fixture_port: int) -> ScenarioResult:
    """Run a single scenario end-to-end and return the result."""
    run_name = f"{RUN_PREFIX}{scenario.name}"
    prompt = _inject_port(scenario.prompt, fixture_port)
    _write_run_config(run_name, prompt, scenario)
    _set_active_run(run_name)
    driver = GenerateScriptDriver()
    exit_code = _run_driver(driver)
    run_path = _runs_path() / run_name
    script_path, smoke_output = _run_emitted_script(run_path)
    result = verify(
        scenario.name,
        scenario.expected,
        run_path,
        smoke_output,
        exit_code,
        str(script_path) if script_path else None,
    )
    logger.info(
        "[robustness] {name}: success={ok} records={n} pdfs={p}",
        name=scenario.name,
        ok=result.success,
        n=result.record_count,
        p=result.pdf_count,
    )
    return result


def _inject_port(prompt: str, port: int) -> str:
    """Replace 8765 in the prompt with the actual fixture server port."""
    return prompt.replace("127.0.0.1:8765", f"127.0.0.1:{port}")


def _write_run_config(run_name: str, prompt: str, scenario: RobustnessScenario) -> None:
    """Write the run config YAML for the scenario."""
    config: dict[str, object] = {"prompt": prompt}
    if scenario.difficulty >= 8:
        config["parallel_runners"] = 4
    yaml_path = PROMPTS_PATH / f"{run_name}.yaml"
    yaml_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")


def _set_active_run(run_name: str) -> None:
    """Point ``data/active_run.yaml`` at the current run."""
    RUNS_FILE.write_text(yaml.dump({"active_run": f"{run_name}.yaml"}), encoding="utf-8")


def _run_driver(driver: GenerateScriptDriver) -> int:
    """Invoke the generation pipeline and return its exit code."""
    logger.info("[robustness] running step_0 driver...")
    start = time.time()
    try:
        exit_code = driver.run([])
    except Exception as exc:
        logger.exception("[robustness] driver crashed: {exc}", exc=exc)
        return 2
    elapsed = time.time() - start
    logger.info("[robustness] driver completed (exit={code}) in {t:.1f}s", code=exit_code, t=elapsed)
    return exit_code


def _run_emitted_script(run_path: Path) -> tuple[Path | None, str]:
    """Run the most recent .py script under run_path/scripts/ and return output."""
    script_path = _find_emitted_script(run_path)
    if script_path is None:
        return None, "[no emitted script found]"
    logger.info("[robustness] running emitted script: {path}", path=script_path)
    env = {**os.environ, **_HEADLESS_ENV, "BROWSER_AGENT_SAVE_RECORD_DB_PATH": str(run_path / "metadata.db")}
    try:
        proc = asyncio.run(_run_subprocess(script_path, env))
    except Exception as exc:
        return script_path, f"[emitted script crashed: {exc}]"
    return script_path, proc


async def _run_subprocess(script_path: Path, env: dict[str, str]) -> str:
    """Run the emitted script as a subprocess with timeout."""
    cmd = [sys.executable, str(script_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as exc:
        return f"[failed to launch emitted script: {exc}]"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=EMITTED_SCRIPT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[emitted script timed out after {EMITTED_SCRIPT_TIMEOUT_S}s]"
    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    return output


def _find_emitted_script(run_path: Path) -> Path | None:
    """Find the most recent processing script under run_path/scripts/."""
    scripts_dir = run_path / "scripts"
    if not scripts_dir.is_dir():
        return None
    candidates = sorted(scripts_dir.glob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        if "discover" in p.name:
            continue
        return p
    return candidates[0] if candidates else None


def _runs_path() -> Path:
    """Return the data/runs/ directory."""
    from browser_agent.configuration import RUNS_PATH

    return RUNS_PATH
