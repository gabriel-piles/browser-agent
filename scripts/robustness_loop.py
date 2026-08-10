"""Main robustness loop driver.

Ties together the fixture server, scenario runner, fix agent, and
scenario generator into an autonomous loop that progressively tests
and hardens the script generation pipeline.

Flow per iteration:
1. Run the scenario (generation pipeline + emitted script + verify).
2. On success: increment consecutive passes; generate next harder scenario.
3. On failure: diagnose → patch → re-run (up to MAX_FIX_ATTEMPTS).
4. After each fix, regression-test the last 3 passing scenarios.
5. Stop after CONSECUTIVE_PASSES_TO_STOP or MAX_LOOP_ITERATIONS.

Constants only — no CLI args per AGENTS.md.
Run via: ``python scripts/robustness_loop.py``
"""

from __future__ import annotations

import json
import os

import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

from browser_agent.configuration import PROJECT_ROOT

sys.path.insert(0, str(Path(__file__).parent))
from fix_agent import apply_patch, diagnose_and_fix  # noqa: E402
from generate_scenario import generate_next_scenario  # noqa: E402
from run_scenario import run_scenario  # noqa: E402


from browser_agent.domain.robustness_scenario import RobustnessScenario


MAX_LOOP_ITERATIONS = 100
MAX_FIX_ATTEMPTS_PER_SCENARIO = 3
CONSECUTIVE_PASSES_TO_STOP = 5
REGRESSION_WINDOW = 3
MAX_CONSECUTIVE_FIX_FAILURES = 3

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
RESULTS_LOG = Path(__file__).parent / "robustness_results.jsonl"
CHROMIUM_PATH = "/usr/bin/chromium"


def main() -> None:
    """Run the robustness loop."""
    _preflight()
    server_proc = _start_fixture_server()
    consecutive_passes = 0
    consecutive_fix_failures = 0
    passing_scenarios: list[RobustnessScenario] = []
    scenario_queue = _load_seed_scenarios()
    fixture_port = _detect_port()
    if fixture_port is None:
        logger.error("[robustness] fixture server not responding")
        _stop_server(server_proc)
        return
    logger.info("[robustness] fixture server on port {port}", port=fixture_port)
    for iteration in range(1, MAX_LOOP_ITERATIONS + 1):
        if not scenario_queue:
            break
        scenario = scenario_queue.pop(0)
        logger.info(
            "[robustness] iteration {i}: scenario={name} difficulty={d}",
            i=iteration,
            name=scenario.name,
            d=scenario.difficulty,
        )
        result = run_scenario(scenario, fixture_port)
        _log_result(iteration, scenario, result, None, None)
        if result.success:
            consecutive_passes += 1
            consecutive_fix_failures = 0
            passing_scenarios.append(scenario)
            if len(passing_scenarios) > REGRESSION_WINDOW:
                passing_scenarios = passing_scenarios[-REGRESSION_WINDOW:]
            if consecutive_passes >= CONSECUTIVE_PASSES_TO_STOP:
                logger.info("[robustness] {n} consecutive passes — stopping", n=consecutive_passes)
                break
        else:
            consecutive_passes = 0
            fixed = _attempt_fix(scenario, result, fixture_port, passing_scenarios)
            if not fixed:
                consecutive_fix_failures += 1
                if consecutive_fix_failures >= MAX_CONSECUTIVE_FIX_FAILURES:
                    logger.error(
                        "[robustness] {n} consecutive fix failures — needs human intervention", n=consecutive_fix_failures
                    )
                    break
            else:
                consecutive_fix_failures = 0
        next_scenario = _generate_next(scenario, result, fixture_port)
        if next_scenario is not None:
            scenario_queue.append(next_scenario)
    _stop_server(server_proc)
    _print_summary()


def _attempt_fix(scenario: RobustnessScenario, result, fixture_port: int, passing: list[RobustnessScenario]) -> bool:
    """Try up to MAX_FIX_ATTEMPTS to fix the failing scenario. Return True if fixed."""
    for attempt in range(1, MAX_FIX_ATTEMPTS_PER_SCENARIO + 1):
        logger.info(
            "[robustness] fix attempt {a}/{m} for {name}", a=attempt, m=MAX_FIX_ATTEMPTS_PER_SCENARIO, name=scenario.name
        )
        _git_checkpoint()
        diagnosis = diagnose_and_fix(result, scenario.prompt)
        if diagnosis is None:
            logger.warning("[robustness] fix agent returned no diagnosis")
            _git_revert()
            continue
        changed = apply_patch(diagnosis)
        if not changed:
            logger.warning("[robustness] fix agent produced no changes")
            _git_revert()
            continue
        re_result = run_scenario(scenario, fixture_port)
        _log_result(0, scenario, re_result, diagnosis, changed)
        if re_result.success:
            logger.info("[robustness] fix succeeded on attempt {a}", a=attempt)
            if _regression_check(passing, fixture_port):
                return True
            logger.warning("[robustness] regression detected — reverting patch")
            _git_revert()
            return False
        _git_revert()
    return False


def _regression_check(passing: list[RobustnessScenario], fixture_port: int) -> bool:
    """Re-run recent passing scenarios; return False if any regresses."""
    for prev in passing:
        result = run_scenario(prev, fixture_port)
        if not result.success:
            logger.warning("[robustness] regression: {name} now fails", name=prev.name)
            return False
    return True


def _git_checkpoint() -> None:
    """Create a git stash checkpoint before a patch."""
    try:
        subprocess.run(
            ["git", "stash", "create"],
            capture_output=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
    except Exception:
        pass


def _git_revert() -> None:
    """Revert the last patch via git checkout."""
    try:
        subprocess.run(
            ["git", "checkout", "--", "src/"],
            capture_output=True,
            cwd=PROJECT_ROOT,
            timeout=10,
        )
    except Exception:
        pass


def _generate_next(scenario: RobustnessScenario, result, fixture_port: int) -> RobustnessScenario | None:
    """Generate the next scenario; escalate difficulty on pass, probe twist on fix."""
    failures = _read_failures_log()
    next_difficulty = min(scenario.difficulty + 1, 8) if result.success else scenario.difficulty
    return generate_next_scenario(next_difficulty, failures)


def _read_failures_log() -> list[dict]:
    """Read the failures log as a list of dicts."""
    log_path = Path(__file__).parent / "failures_log.jsonl"
    if not log_path.is_file():
        return []
    entries: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _load_seed_scenarios() -> list[RobustnessScenario]:
    """Load all seed scenarios from scripts/fixtures/*/manifest.json, sorted by difficulty."""
    scenarios: list[RobustnessScenario] = []
    for manifest in sorted(FIXTURES_ROOT.glob("*/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        scenarios.append(RobustnessScenario(**data))
    scenarios.sort(key=lambda s: s.difficulty)
    return scenarios


def _log_result(iteration: int, scenario: RobustnessScenario, result, diagnosis, changed) -> None:
    """Append one JSON line to the results log."""
    entry: dict[str, object] = {
        "iteration": iteration,
        "scenario": scenario.name,
        "difficulty": scenario.difficulty,
        "success": result.success,
        "failures": result.failures,
        "driver_exit_code": result.driver_exit_code,
        "record_count": result.record_count,
        "pdf_count": result.pdf_count,
        "files_changed": changed or [],
        "diagnosis": diagnosis.get("root_cause", "") if diagnosis else "",
    }
    with open(RESULTS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _preflight() -> None:
    """Check prerequisites: OLLAMA_API_KEY, Chromium."""
    load_dotenv = __import__("dotenv").load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.environ.get("OLLAMA_API_KEY"):
        logger.error("[robustness] OLLAMA_API_KEY not set — exiting")
        raise SystemExit(1)
    if not Path(CHROMIUM_PATH).exists():
        logger.warning("[robustness] Chromium not at {path} — browser scenarios will fail", path=CHROMIUM_PATH)


def _start_fixture_server() -> subprocess.Popen:
    """Start the fixture server as a background process."""
    server_script = Path(__file__).parent / "fixture_server.py"
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(1.0)
    return proc


def _detect_port() -> int | None:
    """Detect which port the fixture server is listening on."""
    import urllib.request

    for port in range(8765, 8776):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return port
        except Exception:
            continue
    return None


def _stop_server(proc: subprocess.Popen) -> None:
    """Stop the fixture server process."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _print_summary() -> None:
    """Print a summary of the results log."""
    if not RESULTS_LOG.is_file():
        return
    lines = RESULTS_LOG.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    passes = sum(1 for line in lines if json.loads(line).get("success"))
    logger.info("[robustness] summary: {p}/{t} passes over {t} iterations", p=passes, t=total)


if __name__ == "__main__":
    main()
