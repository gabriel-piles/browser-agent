"""Generate the next scenario using an LLM one-shot call.

Reads the failure history to understand what patterns are failing,
then generates a new scenario description + HTML fixtures that probe
a gap not yet covered. Uses the project's ``OllamaAdapter.get_model()``
+ ``pydantic_ai.Agent(output_type=dict)`` for structured JSON output.

The generated scenario is written to ``scripts/fixtures/<name>/``.
Constants only — no CLI args per AGENTS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic_ai import Agent

from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.domain.expected_output import ExpectedOutput
from browser_agent.domain.robustness_scenario import RobustnessScenario

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
FAILURES_LOG = Path(__file__).parent / "failures_log.jsonl"
RESULTS_LOG = Path(__file__).parent / "robustness_results.jsonl"

_SYSTEM_PROMPT = """\
You are a test scenario generator for a web scraping script generation pipeline.

The pipeline produces self-contained Python scripts using zendriver (a CDP browser
automation library) and a set of helper functions from script_tools/. The helpers
include: discover_links (scroll + load-more), save_record (SQLite persistence),
download_pdf, start_browser, wait_for_anchors, select_filter_value, etc.

Your job: generate a NEW test scenario that probes a gap not yet covered by the
existing scenarios. The scenario must be a local HTML fixture that the generation
pipeline can be tested against.

Available scenario patterns (escalating difficulty 1-8):
1. single_page_list — basic extraction, save_record, CSS selectors
2. multi_page_pagination — pagination loop via Next button
3. dropdown_filter — select dropdown, filter iteration, dedup
4. infinite_scroll — AJAX-loaded items, scroll + load-more button
5. spa_dynamic — JS-rendered content, wait_for_page_ready
6. pdf_download_modal — modal/button revealing PDF links, PDF download
7. mixed_content — PDF + HTML links, document type classification
8. concurrency — parallel processing with multiple browser tabs

Generate a scenario at the requested difficulty level. Return JSON:
{
  "name": "scenario_slug",
  "difficulty": <int 1-8>,
  "pattern": "what_site_pattern_it_tests",
  "prompt": "Natural-language scraping task pointing at http://127.0.0.1:8765/?scenario=<name>",
  "description": "What this scenario probes",
  "fixture_files": {"index.html": "<html>...</html>", "manifest.json": "{...}"},
  "expected": {"min_records": <int>, "required_fields": ["field1"], "pdf_count": <int>, "description": "..."}
}

Rules:
- The prompt URL must start with http://127.0.0.1:8765/?scenario=<name>
- Include enough items to test the pattern meaningfully (at least 10)
- For PDF scenarios, reference /pdf/<name>.pdf paths (the runner creates dummy PDFs)
- The manifest.json must match the RobustnessScenario schema
- Keep HTML simple but realistic — no React, no external resources
"""


def generate_next_scenario(difficulty: int, failure_history: list[dict]) -> RobustnessScenario | None:
    """Generate the next scenario at the given difficulty level."""
    try:
        model = OllamaAdapter().get_model()
    except Exception as exc:
        logger.warning("[robustness] could not init LLM for scenario generation: {exc}", exc=exc)
        return None
    agent: Agent = Agent(model, output_type=dict, system_prompt=_SYSTEM_PROMPT)
    context = _build_context(difficulty, failure_history)
    try:
        run_result = agent.run_sync(context)
    except Exception as exc:
        logger.warning("[robustness] scenario generation LLM call failed: {exc}", exc=exc)
        return None
    result = run_result.output
    return _persist_scenario(result, difficulty)


def _build_context(difficulty: int, failure_history: list[dict]) -> str:
    """Build the user prompt for the scenario generation LLM call."""
    failures_summary = "No failures recorded yet."
    if failure_history:
        recent = failure_history[-5:]
        failures_summary = json.dumps(recent, indent=2)
    return (
        f"Generate a NEW scenario at difficulty level {difficulty}. "
        f"Here are the recent failure patterns:\n{failures_summary}\n\n"
        f"Design a scenario that probes a gap not yet covered."
    )


def _persist_scenario(result: dict, difficulty: int) -> RobustnessScenario | None:
    """Write the generated fixture files to disk and return a RobustnessScenario."""
    name = result.get("name", f"generated_{difficulty}")
    fixture_dir = FIXTURES_ROOT / name
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_files = result.get("fixture_files", {})
    for fname, content in fixture_files.items():
        if not isinstance(content, str):
            continue
        (fixture_dir / fname).write_text(content, encoding="utf-8")
    expected_data = result.get("expected", {})
    expected = ExpectedOutput(
        min_records=expected_data.get("min_records", 1),
        required_fields=expected_data.get("required_fields", []),
        pdf_count=expected_data.get("pdf_count", 0),
        description=expected_data.get("description", ""),
    )
    scenario = RobustnessScenario(
        name=name,
        difficulty=difficulty,
        pattern=result.get("pattern", ""),
        prompt=result.get("prompt", ""),
        fixture_dir=f"scripts/fixtures/{name}",
        expected=expected,
        description=result.get("description", ""),
    )
    _ensure_manifest(fixture_dir, scenario)
    return scenario


def _ensure_manifest(fixture_dir: Path, scenario: RobustnessScenario) -> None:
    """Ensure a manifest.json exists in the fixture directory."""
    manifest_path = fixture_dir / "manifest.json"
    if manifest_path.exists():
        return
    manifest = {
        "name": scenario.name,
        "difficulty": scenario.difficulty,
        "pattern": scenario.pattern,
        "prompt": scenario.prompt,
        "fixture_dir": scenario.fixture_dir,
        "expected": scenario.expected.model_dump(),
        "description": scenario.description,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
