"""Scan data/runs/ for prior scripts with their explanations and outcomes.

Feeds the planner and builder agents with proven patterns from similar
past runs so they can skip re-deriving mechanics they already observed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loguru import logger

from browser_agent.configuration import RUNS_PATH
from browser_agent.domain.prior_script_summary import PriorScriptSummary

_MAX_SCRIPTS_PER_RUN = 6
_MAX_TOTAL_RESULTS = 8
_MAX_SUMMARY_CHARS = 2000
_MAX_SOURCE_LINES = 200


class PriorScriptsIndex:
    """Scans prior runs and returns relevant script summaries for a task."""

    def __init__(self, current_run_path: Path | None = None) -> None:
        self._current = current_run_path
        self._runs_root = RUNS_PATH

    def find_relevant(
        self,
        task: str,
        kind: str | None = None,
        max_results: int = _MAX_TOTAL_RESULTS,
    ) -> list[PriorScriptSummary]:
        summaries = self._scan_runs()
        if kind:
            summaries = [s for s in summaries if s.kind == kind]
        scored = [(self._score(task, s), s) for s in summaries]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:max_results] if _ > 0]

    def render_context(self, summaries: list[PriorScriptSummary]) -> str:
        if not summaries:
            return ""
        parts: list[str] = [
            "Prior scripts from similar runs (suggestions only — always re-verify selectors):",
        ]
        for s in summaries:
            status_note = ""
            if s.status == "verification_failed":
                status_note = " [HAD GAPS — structure worked, selectors may need adjustment]"
            elif s.status == "accepted_gap":
                status_note = " [ACCEPTED GAPS — mostly correct, known small misses]"
            selectors_str = ""
            if s.verified_selectors:
                top = s.verified_selectors[:3]
                selectors_str = "; selectors: " + ", ".join(f'"{sel}"' for sel in top)
            parts.append(
                f"- {s.run_name}/{s.script_path} — {s.subtask_description[:120]} "
                f"({s.kind}, {s.pdf_download_strategy}, {s.status}{status_note})"
                f"{selectors_str}"
            )
        return "\n".join(parts)

    def source_blocks(self, nominated: list[str]) -> str:
        """Render full source of nominated prior scripts, truncated.

        nominated entries are "<run_name>/<script_path>" strings as
        rendered by render_context. Unresolvable ids are skipped with a
        warning. Returns "" when nothing resolves.
        """
        lookup = {f"{s.run_name}/{s.script_path}": s for s in self._scan_runs()}
        parts: list[str] = []
        for nomination in nominated:
            summary = lookup.get(nomination)
            if summary is None:
                logger.warning(
                    "prior_scripts_index: nominated id not found: {id}",
                    id=nomination,
                )
                continue
            block = self._source_block(summary)
            if block:
                parts.append(block)
        return "\n\n".join(parts)

    def _source_block(self, summary: PriorScriptSummary) -> str:
        path = self._runs_root / summary.run_name / summary.script_path
        if not path.is_file():
            logger.warning(
                "prior_scripts_index: nominated script file missing: {path}",
                path=str(path),
            )
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        truncated = len(lines) > _MAX_SOURCE_LINES
        body = "\n".join(lines[:_MAX_SOURCE_LINES])
        suffix = f"\n... ({len(lines) - _MAX_SOURCE_LINES} more lines)" if truncated else ""
        return (
            f"## Nominated prior script: {summary.run_name}/{summary.script_path}\n"
            f"(status: {summary.status}) — proven starting point; adapt as "
            "needed, keep the parts that make sense.\n"
            f"```python\n{body}{suffix}\n```"
        )

    def _scan_runs(self) -> list[PriorScriptSummary]:
        summaries: list[PriorScriptSummary] = []
        if not self._runs_root.is_dir():
            return summaries
        for run_dir in sorted(self._runs_root.iterdir()):
            if not run_dir.is_dir():
                continue
            if self._current and run_dir.resolve() == self._current.resolve():
                continue
            try:
                summaries.extend(self._read_run(run_dir))
            except Exception:
                logger.debug("prior_scripts_index: failed to read run {run}", run=run_dir.name)
        return summaries

    def _read_run(self, run_dir: Path) -> list[PriorScriptSummary]:
        run_name = run_dir.name
        metadata = self._read_task_split(run_dir)
        if metadata is None:
            metadata = self._read_flow_state(run_dir)
        if metadata is None:
            metadata = self._read_run_yaml(run_dir)

        scripts_dir = run_dir / "scripts"
        if not scripts_dir.is_dir():
            return []

        scripts = sorted(
            p for p in scripts_dir.glob("*.py") if re.match(r"\d{4}_\d{2}_\d{2}", p.name) and not p.name.endswith(".raw.py")
        )
        if not scripts:
            return []

        summaries: list[PriorScriptSummary] = []
        for script_path in scripts[:_MAX_SCRIPTS_PER_RUN]:
            kind = "discovery" if "__discover__" in script_path.name else "processing"
            desc = metadata.get("task_summary", "") if metadata else ""
            if metadata and kind == "discovery":
                desc = _first_sentence(metadata.get("discovery_prompt", desc))
            elif metadata and kind == "processing":
                desc = _first_sentence(metadata.get("processing_prompt", desc))

            try:
                rel_script_path = str(script_path.relative_to(run_dir))
            except ValueError:
                rel_script_path = str(script_path)
            summaries.append(
                PriorScriptSummary(
                    run_name=run_name,
                    script_path=rel_script_path,
                    kind=kind,
                    task_summary=_truncate(metadata.get("task_summary", ""), _MAX_SUMMARY_CHARS) if metadata else "",
                    subtask_description=_truncate(desc, _MAX_SUMMARY_CHARS),
                    verified_selectors=metadata.get("verified_selectors", []) if metadata else [],
                    pdf_download_strategy=(
                        metadata.get("pdf_download_strategy", "browser_fetch") if metadata else "browser_fetch"
                    ),
                    status=metadata.get("status", "unknown") if metadata else "unknown",
                    site_overview=_truncate(metadata.get("site_overview", ""), _MAX_SUMMARY_CHARS) if metadata else "",
                )
            )
        return summaries

    @staticmethod
    def _read_task_split(run_dir: Path) -> dict | None:
        path = run_dir / "task_split.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["task_summary"] = _first_sentence(data.get("processing_prompt", data.get("site_overview", "")))
            data["status"] = "unknown"
            return data
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _read_flow_state(run_dir: Path) -> dict | None:
        path = run_dir / "flow" / "state.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = data.get("plan", {})
            records = data.get("records", [])
            record_map = {r["subtask_id"]: r for r in records}
            result: dict = {
                "task_summary": plan.get("task_summary", ""),
                "site_overview": plan.get("site_overview", ""),
            }
            for spec in plan.get("subtasks", []):
                sid = spec.get("subtask_id", "")
                rec = record_map.get(sid, {})
                result.setdefault("verified_selectors", []).extend(spec.get("verified_selectors", []))
                if spec.get("kind") == "discovery":
                    result["discovery_prompt"] = spec.get("description", "")
                    result.setdefault("status_by_kind", {})["discovery"] = rec.get("status", "unknown")
                else:
                    result["processing_prompt"] = spec.get("description", "")
                    result.setdefault("status_by_kind", {})["processing"] = rec.get("status", "unknown")
            result["pdf_download_strategy"] = (
                plan.get("subtasks", [{}])[0].get("pdf_download_strategy", "browser_fetch")
                if plan.get("subtasks")
                else "browser_fetch"
            )
            result["status"] = "unknown"
            return result
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    @staticmethod
    def _read_run_yaml(run_dir: Path) -> dict | None:
        for yaml_file in run_dir.glob("*.yaml"):
            try:
                text = yaml_file.read_text(encoding="utf-8")
                m = re.search(r"prompt:\s*\|\s*\n(.+)", text, re.DOTALL)
                if m:
                    prompt = m.group(1).strip()
                    first = _first_sentence(prompt)
                    return {
                        "task_summary": first,
                        "site_overview": "",
                        "status": "unknown",
                    }
            except OSError:
                pass
        return None

    @staticmethod
    def _score(task: str, summary: PriorScriptSummary) -> int:
        task_lower = task.lower()
        score = 0
        for term in _keywords(task_lower):
            score += summary.task_summary.lower().count(term) * 3
            score += summary.subtask_description.lower().count(term) * 2
            score += summary.site_overview.lower().count(term)
        return score


def _keywords(text: str) -> list[str]:
    tokens = re.findall(r"[a-z]{4,}", text.lower())
    stop = {
        "this",
        "that",
        "with",
        "from",
        "each",
        "have",
        "been",
        "they",
        "will",
        "what",
        "when",
        "where",
        "which",
        "there",
        "their",
        "about",
        "should",
        "would",
        "could",
        "into",
        "also",
        "than",
        "then",
        "just",
        "only",
        "over",
        "very",
        "your",
        "some",
    }
    return [t for t in tokens if t not in stop][:20]


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _first_sentence(text: str) -> str:
    m = re.match(r"^([^.!?\n]+[.!?\n])", text.strip())
    return m.group(1).strip() if m else text.strip()[:200]
