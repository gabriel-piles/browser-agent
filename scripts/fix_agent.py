"""Autonomous diagnosis + patch agent for the robustness loop.

Given a failing ``ScenarioResult``, this agent:
1. Collects evidence (failure output, emitted script, scenario prompt).
2. Diagnoses root cause via an LLM one-shot call.
3. Produces a patch (file edits) scoped to the allowed files.
4. The runner applies the patch and re-runs the scenario.

The fix agent CANNOT edit the driver orchestration, domain models,
or the test harness itself. It CAN edit: system prompts, linter
rules, error patterns, script_tools helpers, repair prompts, and
configuration timeouts.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic_ai import Agent

from browser_agent.adapters.llm.llm_adapter_factory import build_llm
from browser_agent.domain.scenario_result import ScenarioResult

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
FAILURES_LOG = Path(__file__).parent / "failures_log.jsonl"

_SRC = Path(__file__).resolve().parent.parent / "src" / "browser_agent"

_ALLOWED_FILES = [
    "use_cases/planner_system_prompt.py",
    "use_cases/builder_system_prompt.py",
    "use_cases/orchestrator_system_prompt.py",
    "use_cases/emitted_script_linter.py",
    "use_cases/zendriver_error_patterns.py",
    "use_cases/script_repair_prompt.py",
    "configuration.py",
]
_SCRIPT_TOOLS_DIR = _SRC / "script_tools"

_SYSTEM_PROMPT = """\
You are a debugging agent for a web scraping script generation pipeline.

The pipeline has 3 LLM agents (Explorer → Discovery Writer → Processing Writer) that
generate self-contained Python scraping scripts. When a generated script fails, you
must diagnose the root cause and propose a patch to the PIPELINE (not the script itself).

You CAN edit:
- System prompts (explorer, discovery_writer, processing_writer)
- Linter rules (emitted_script_linter.py)
- Zendriver error patterns (zendriver_error_patterns.py)
- Script repair prompts (script_repair_prompt.py)
- Script tools helpers (script_tools/*.py)
- Configuration (configuration.py — timeouts, limits)

You CANNOT edit:
- step_0_run_prompt.py (driver orchestration)
- domain/*.py (pydantic models — output contract is stable)
- scripts/*.py (test harness)

Diagnose the failure and return JSON:
{
  "root_cause": "short description of the root cause",
  "affected_files": ["path/to/file.py"],
  "reasoning": "why this fix addresses the root cause",
  "files_to_edit": [
    {"path": "relative/path.py", "old_snippet": "exact text to find", "new_snippet": "replacement text"}
  ]
}

Rules:
- `path` in files_to_edit is relative to src/browser_agent/ (e.g. "use_cases/processing_writer_system_prompt.py")
  or "script_tools/<module>.py" for helpers.
- `old_snippet` must be an EXACT substring of the file (enough context to be unique).
- `new_snippet` replaces `old_snippet` verbatim.
- Prefer small, surgical patches. Do NOT rewrite entire files.
- If the failure is in the agent's UNDERSTANDING of the site pattern, fix the prompt.
- If the failure is a RUNTIME error (zendriver API misuse), add an error pattern or fix the helper.
- If the failure is a LINT miss (script passes lint but crashes), add a linter rule.
"""


def diagnose_and_fix(result: ScenarioResult, scenario_prompt: str) -> dict | None:
    """Diagnose a failure and return a patch dict, or None on error."""
    try:
        model = build_llm().get_model()
    except Exception as exc:
        logger.warning("[robustness] fix agent: could not init LLM: {exc}", exc=exc)
        return None
    agent: Agent = Agent(model, output_type=dict, system_prompt=_SYSTEM_PROMPT)
    evidence = _collect_evidence(result, scenario_prompt)
    try:
        run_result = agent.run_sync(evidence)
    except Exception as exc:
        logger.warning("[robustness] fix agent: LLM call failed: {exc}", exc=exc)
        return None
    diagnosis = run_result.output
    _log_diagnosis(result.scenario_name, diagnosis)
    return diagnosis


def apply_patch(diagnosis: dict) -> list[str]:
    """Apply the files_to_edit from a diagnosis and return list of changed files."""
    changed: list[str] = []
    for edit_spec in diagnosis.get("files_to_edit", []):
        path_str = edit_spec.get("path", "")
        old = edit_spec.get("old_snippet", "")
        new = edit_spec.get("new_snippet", "")
        if not path_str or not old:
            continue
        file_path = _resolve_path(path_str)
        if file_path is None:
            logger.warning("[robustness] fix agent: skipping disallowed path {p}", p=path_str)
            continue
        if not file_path.is_file():
            logger.warning("[robustness] fix agent: file not found {p}", p=file_path)
            continue
        content = file_path.read_text(encoding="utf-8")
        if old not in content:
            logger.warning("[robustness] fix agent: old_snippet not found in {p}", p=file_path)
            continue
        patched = content.replace(old, new, 1)
        file_path.write_text(patched, encoding="utf-8")
        changed.append(str(file_path))
        logger.info("[robustness] fix agent: patched {p}", p=file_path)
    return changed


def _resolve_path(path_str: str) -> Path | None:
    """Resolve a path string relative to src/browser_agent/, checking allow-list."""
    candidate = _SRC / path_str
    if not candidate.is_file():
        candidate = _SCRIPT_TOOLS_DIR / path_str
    if not candidate.is_file():
        return None
    resolved = candidate.resolve()
    if not str(resolved).startswith(str(_SRC.resolve())):
        return None
    rel = str(resolved.relative_to(_SRC.resolve()))
    if rel in _ALLOWED_FILES or rel.startswith("script_tools/"):
        return resolved
    return None


def _collect_evidence(result: ScenarioResult, scenario_prompt: str) -> str:
    """Build the evidence string for the fix agent's LLM call."""
    parts = [
        f"Scenario: {result.scenario_name}",
        f"Driver exit code: {result.driver_exit_code}",
        f"Record count: {result.record_count}",
        f"PDF count: {result.pdf_count}",
        f"Failures: {json.dumps(result.failures, indent=2)}",
        f"Scenario prompt: {scenario_prompt}",
        f"Smoke output (last 2000 chars):\n{result.smoke_output[-2000:]}",
    ]
    if result.emitted_script_path:
        script_path = Path(result.emitted_script_path)
        if script_path.is_file():
            script = script_path.read_text(encoding="utf-8")
            parts.append(f"Emitted script (last 3000 chars):\n{script[-3000:]}")
    parts.append(_read_prompt_snippets())
    return "\n\n".join(parts)


def _read_prompt_snippets() -> str:
    """Read relevant source files so the LLM can generate exact old_snippet matches."""
    files = [
        _SRC / "use_cases" / "processing_writer_system_prompt.py",
        _SRC / "use_cases" / "discovery_writer_system_prompt.py",
        _SRC / "use_cases" / "explorer_system_prompt.py",
        _SRC / "script_tools" / "save_record.py",
    ]
    snippets: list[str] = []
    for f in files[:2]:
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            snippets.append(f"--- {f.relative_to(_SRC)} (last 4000 chars) ---\n{text[-4000:]}")
    for f in files[2:]:
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            snippets.append(f"--- {f.relative_to(_SRC)} (last 2000 chars) ---\n{text[-2000:]}")
    return "\n\n".join(snippets)


def _log_diagnosis(scenario_name: str, diagnosis: dict) -> None:
    """Append the diagnosis to the failures log."""
    entry = {
        "scenario": scenario_name,
        "root_cause": diagnosis.get("root_cause", ""),
        "affected_files": diagnosis.get("affected_files", []),
        "reasoning": diagnosis.get("reasoning", ""),
        "files_to_edit_count": len(diagnosis.get("files_to_edit", [])),
    }
    with open(FAILURES_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
