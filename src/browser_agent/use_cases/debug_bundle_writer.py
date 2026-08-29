"""Synthesize a self-contained debug bundle for one completed/crashed run.

Aggregates already-persisted evidence — flow state, orchestrator
decisions, per-subtask reports and the final verification — into
``debug/README.md`` + ``debug/manifest.json`` and copies representative
saved HTML files, so a coding agent can debug the run without re-running
the multi-hour flow. Purely additive: it reads existing artifacts and
never mutates them.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from browser_agent.domain.orchestrator_state import OrchestratorState
from browser_agent.domain.run_config import RunConfig
from browser_agent.domain.script_execution_report import ScriptExecutionReport
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.drivers.flow.flow_paths import FlowPaths
from browser_agent.use_cases.flow_state_store import FlowStateStore

_HTML_EXAMPLES_MAX = 20
_README = "README.md"
_MANIFEST = "manifest.json"
_OUTCOME_LABELS = {
    "finished": "Finished normally",
    "failed": "Finished with non-zero exit code",
    "crashed": "Crashed (unhandled exception)",
    "interrupted_by_sigint": "Stopped by user (Ctrl-C)",
    "interrupted_by_sigterm": "Stopped by SIGTERM",
    "interrupted_by_sighup": "Stopped by SIGHUP",
}
_FAILURE_STATUSES = {
    "lint_failed",
    "smoke_failed",
    "execution_failed",
    "verification_failed",
    "repair_noop",
    "aborted",
    "emit_budget_exhausted",
}
_LOCATION_PATHS = (
    "flow/state.json",
    "flow/decisions.jsonl",
    "flow/plan_*.json",
    "flow/subtasks/<id>/*.json|md",
    "flow/subtasks/<id>/execution_live.log (full script stdout+stderr)",
    "scripts/*.py|.raw.py|.json",
    "downloads/ (PDFs + html_*.html)",
    "logs/run.log (all loguru output incl. tracebacks)",
    "logs/stall_dump.log",
    "metadata.db",
    "verification_report.{md,json}",
    "reconciler_inventory.{md,json}",
    "debug/llm/",
    "debug/html_examples/",
)


class DebugBundleWriter:
    """Build ``debug/README.md`` + ``debug/manifest.json`` for one run."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def write(self, run: RunConfig, outcome: str, error_text: str = "") -> Path:
        debug = self._debug_dir()
        ctx = self._collect_context(debug)
        _atomic_write_text(debug / _README, _render_readme(run, outcome, error_text, **ctx))
        _atomic_write_text(debug / _MANIFEST, json.dumps(_render_manifest(run, outcome, error_text, **ctx), indent=2))
        return debug

    def _collect_context(self, debug: Path) -> dict:
        return {
            "state": self._read_state(),
            "decisions": self._read_decisions(),
            "verification": self._read_verification_report(),
            "subtasks": self._collect_subtask_files(),
            "html_count": self._collect_html_examples(debug),
            "llm_count": self._llm_transcript_count(debug),
        }

    def _debug_dir(self) -> Path:
        path = self._run_path / "debug"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _read_state(self) -> OrchestratorState | None:
        return FlowStateStore(FlowPaths(self._run_path)).load()

    def _read_decisions(self) -> list[dict]:
        path = self._run_path / "flow" / "decisions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _read_verification_report(self) -> VerificationReport | None:
        path = self._run_path / "verification_report.json"
        if not path.exists():
            return None
        return VerificationReport.model_validate_json(path.read_text(encoding="utf-8"))

    def _collect_subtask_files(self) -> list[dict]:
        root = self._run_path / "flow" / "subtasks"
        if not root.exists():
            return []
        return [_subtask_file_entry(d) for d in sorted(root.iterdir()) if d.is_dir()]

    def _collect_html_examples(self, debug: Path) -> int:
        dest = debug / "html_examples"
        dest.mkdir(parents=True, exist_ok=True)
        src_dir = self._run_path / "downloads"
        if not src_dir.exists():
            return 0
        files = sorted(src_dir.glob("html_*.html"), key=lambda p: p.stat().st_size, reverse=True)
        for f in files[:_HTML_EXAMPLES_MAX]:
            shutil.copy2(f, dest / f.name)
        return min(len(files), _HTML_EXAMPLES_MAX)

    def _llm_transcript_count(self, debug: Path) -> int:
        llm = debug / "llm"
        if not llm.exists():
            return 0
        return len(list(llm.glob("*.json")))


def _render_readme(run, outcome, error_text, state, decisions, verification, subtasks, html_count, llm_count) -> str:
    entries = _failure_entries(decisions, _subtask_statuses(state))
    return (
        "\n".join(
            _join_sections(
                _header_section(run, outcome, html_count, llm_count),
                _final_result_section(verification),
                _subtasks_section(state, subtasks),
                _failures_section(verification, entries),
                _errors_section(error_text),
                _locations_section(),
                _how_to_debug_section(),
            )
        )
        + "\n"
    )


def _render_manifest(run, outcome, error_text, state, decisions, verification, subtasks, html_count, llm_count) -> dict:
    return {
        "run": run.name,
        "outcome": outcome,
        "outcome_label": _outcome_label(outcome),
        "generated_at": _now_iso(),
        "finished": state.finished if state is not None else False,
        "prompt": run.prompt,
        "registry_template": run.scraper_registry_template,
        "parallel_runners": run.parallel_runners,
        "subtasks": _manifest_subtasks(state, subtasks),
        "verification": _verification_manifest(verification),
        "failures": _failure_entries(decisions, _subtask_statuses(state)),
        "errors": error_text,
        "html_examples_count": html_count,
        "llm_transcripts_count": llm_count,
    }


def _header_section(run, outcome, html_count, llm_count) -> list[str]:
    return [
        f"# Run Debug Bundle — {run.name}",
        "",
        f"Outcome: {_outcome_label(outcome)}  (generated {_now_iso()})",
        "",
        "## What this run was",
        "",
        *_run_facts(run, html_count, llm_count),
        "",
        "```",
        run.prompt,
        "```",
    ]


def _run_facts(run, html_count, llm_count) -> list[str]:
    runners = run.parallel_runners if run.parallel_runners is not None else "default (single-tab)"
    return [
        f"- run: {run.name}",
        f"- scraper_registry_template: {run.scraper_registry_template or 'none'}",
        f"- parallel_runners: {runners}",
        f"- evidence: {llm_count} llm transcripts, {html_count} html examples",
    ]


def _final_result_section(verification) -> list[str]:
    lines = ["## Final result", ""]
    if verification is None:
        lines.append("Final verification did not run (flow did not reach it).")
        return lines
    lines += [
        f"- coverage_complete: {verification.coverage_complete}",
        f"- expected_pdf_total: {verification.expected_pdf_total}",
        f"- observed_pdf_total: {verification.observed_pdf_total}",
        f"- missing_count: {verification.missing_count}",
        f"- overall_assessment: {verification.overall_assessment}",
        f"- recommendations: {verification.recommendations}",
    ]
    return lines


def _subtasks_section(state, subtasks) -> list[str]:
    lines = [
        "## Subtasks",
        "",
        "| subtask_id | kind | status | attempts | emits | verification | script |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = _subtask_rows(state, subtasks)
    if not rows:
        lines.append("| — | — | — | — | — | — | — |")
    for r in rows:
        lines.append(
            f"| {_cell(r['subtask_id'])} | {_cell(r['kind'])} | {_cell(r['status'])} | {r['attempts']} | {r['emits']} | {_cell(r['verification'])} | {_cell(r['script_path'])} |"
        )
    return lines


def _failures_section(verification, entries) -> list[str]:
    lines = ["## Failures & what to fix", ""]
    for e in entries:
        lines.append(
            f"- {e['subtask_id'] or '(plan)'} — action: {_truncate(e['focus'] or e['reasoning'] or e['action'], 1000)}"
        )
    if verification is not None:
        for mc in verification.missing_coverage:
            lines.append(f"- missing_coverage: {mc.navigation_path} — {_truncate(mc.step_0_fix, 1000)}")
        for imp in verification.script_tools_improvements:
            lines.append(f"- script_tools: {_truncate(imp, 1000)}")
    if len(lines) == 2:
        lines.append("No failures recorded.")
    return lines


def _errors_section(error_text) -> list[str]:
    if not error_text:
        return ["## Errors thrown", "", "No unhandled exception."]
    return ["## Errors thrown", "", "```", error_text, "```"]


def _locations_section() -> list[str]:
    return ["## Where everything lives", ""] + [f"- {p}" for p in _LOCATION_PATHS]


def _how_to_debug_section() -> list[str]:
    return [
        "## How to debug",
        "",
        "- Read this README fully.",
        "- Open flow/decisions.jsonl and the per-subtask verification_report.md / execution_live.log for the subtask to fix.",
        "- Read the matching debug/llm/<seq>_<agent>.json transcript for the exact prompts and tool results the agent reasoned over.",
        "- Inspect debug/html_examples/ (or downloads/*.html) for the actual page markup.",
        "- Fix the relevant use case or script_tools module, then rerun the same command to resume.",
    ]


def _subtask_statuses(state) -> dict[str, Any]:
    if state is None:
        return {}
    return {r.subtask_id: r for r in state.records}


def _subtask_rows(state, subtasks) -> list[dict]:
    statuses = _subtask_statuses(state)
    by_id = _subtask_file_by_id(subtasks)
    plan = state.plan if state is not None else None
    plan_specs = {s.subtask_id: s for s in plan.subtasks} if plan is not None else {}
    if state is not None and state.records:
        ids = [r.subtask_id for r in state.records]
    elif plan_specs:
        ids = list(plan_specs)
    else:
        ids = sorted(by_id)
    return [_subtask_row(sid, statuses.get(sid), by_id.get(sid, {}), plan_specs.get(sid)) for sid in ids]


def _subtask_row(sid, record, subtask_file, plan_spec) -> dict:
    spec = subtask_file.get("spec") or plan_spec
    verification = subtask_file.get("verification")
    execution = subtask_file.get("execution")
    script_path = (record.script_path if record is not None and record.script_path else "") or (
        execution.script_path if execution is not None else ""
    )
    return {
        "subtask_id": sid,
        "kind": spec.kind if spec is not None else "",
        "status": record.status if record is not None else "—",
        "attempts": record.attempts if record is not None else 0,
        "emits": record.emits if record is not None else 0,
        "script_path": script_path or "",
        "verification": _truncate(verification.overall_assessment, 120) if verification is not None else "—",
    }


def _subtask_file_by_id(subtask_files) -> dict[str, dict]:
    return {d["subtask_id"]: d for d in subtask_files}


def _subtask_file_entry(d: Path) -> dict:
    return {
        "subtask_id": d.name,
        "spec": _read_model(d / "subtask.json", SubtaskSpec),
        "verification": _read_model(d / "verification_report.json", VerificationReport),
        "execution": _read_model(d / "execution_report.json", ScriptExecutionReport),
        "smoke": _read_dict(d / "smoke_report.json"),
        "script_tools_feedback": _read_dict(d / "script_tools_feedback.json"),
    }


def _manifest_subtasks(state, subtasks) -> list[dict]:
    return [
        {
            "subtask_id": r["subtask_id"],
            "kind": r["kind"],
            "status": r["status"],
            "attempts": r["attempts"],
            "emits": r["emits"],
            "script_path": r["script_path"],
        }
        for r in _subtask_rows(state, subtasks)
    ]


def _verification_manifest(verification) -> dict | None:
    if verification is None:
        return None
    return {
        "coverage_complete": verification.coverage_complete,
        "expected_pdf_total": verification.expected_pdf_total,
        "observed_pdf_total": verification.observed_pdf_total,
        "missing_count": verification.missing_count,
        "recommendations": verification.recommendations,
    }


def _failure_entries(decisions, record_statuses) -> list[dict]:
    entries = [_decision_failure(d) for d in decisions if _is_failure_decision(d)]
    entries += [
        {"subtask_id": sid, "action": rec.status, "focus": "", "reasoning": ""}
        for sid, rec in record_statuses.items()
        if rec.status in _FAILURE_STATUSES
    ]
    return entries


def _is_failure_decision(item) -> bool:
    decision = item.get("decision") if isinstance(item, dict) else {}
    return isinstance(decision, dict) and decision.get("action") != "accept_plan"


def _decision_failure(item) -> dict:
    decision = item.get("decision") if isinstance(item, dict) else {}
    d = decision if isinstance(decision, dict) else {}
    return {
        "subtask_id": d.get("subtask_id", ""),
        "action": d.get("action", ""),
        "focus": d.get("focus", ""),
        "reasoning": d.get("reasoning", ""),
    }


def _join_sections(*sections) -> list[str]:
    out: list[str] = []
    for section in sections:
        out += section
        out.append("")
    return out


def _read_model(path: Path, cls) -> Any:
    if not path.exists():
        return None
    return cls.model_validate_json(path.read_text(encoding="utf-8"))


def _read_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _outcome_label(outcome) -> str:
    return _OUTCOME_LABELS.get(outcome, outcome)


def _truncate(text, limit) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
