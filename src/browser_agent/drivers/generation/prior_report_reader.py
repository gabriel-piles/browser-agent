"""Read a prior run's ``verification_report.json`` and render feedback for step 0.

When the operator re-runs step 0 after step 2 produced a
``verification_report.json`` with coverage gaps, this reader turns the
machine-readable ``missing_coverage`` entries into a single feedback
block that the step 0 agent sees as leading context — closing the
cross-step repair loop the report was designed for. The class reads ONLY
``verification_report.json``; the reconciler inventory is already
distilled into ``missing_coverage`` by the verification agent.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

_REPORT_JSON_FILENAME = "verification_report.json"
_MAX_GAPS = 12

_FEEDBACK_HEADER = (
    "A PRIOR RUN of this scraper was verified and found the following coverage "
    "gaps. Your script MUST fix every issue below. Each item names the path that "
    "was missed, what was expected vs observed, the root cause, and a concrete "
    "fix. Apply every fix while preserving the parts of the strategy that worked.\n\n"
    "Gaps found: {n}\n"
)


class PriorReportReader:
    """Render ``verification_report.json`` ``missing_coverage`` as feedback text."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path
        self._path = run_path / _REPORT_JSON_FILENAME

    def read(self) -> str:
        """Return rendered feedback, or "" when there is nothing to feed back.

        Returns "" for: no prior report (first run), a clean prior report
        (coverage complete or no missing coverage), or an unparseable report
        (logged as a warning so a corrupt file never blocks generation).
        """
        if not self._path.is_file():
            logger.info("no prior verification report found (first run) — generating from scratch")
            return ""
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "prior verification report at {path} is unparseable ({exc}) — generating from scratch",
                path=self._path,
                exc=str(exc),
            )
            return ""
        if _is_clean(payload):
            logger.info("prior verification report was clean (coverage_complete=true) — generating from scratch")
            return ""
        gaps = payload.get("missing_coverage") or []
        rendered = _render(gaps)
        logger.info(
            "applying prior-run verification feedback: {n} gap(s) from {path}",
            n=min(len(gaps), _MAX_GAPS),
            path=self._path,
        )
        return rendered


def _is_clean(payload: dict[str, object]) -> bool:
    """True when the prior report had no gaps to fix."""
    if payload.get("coverage_complete") is True:
        return True
    if not payload.get("missing_coverage"):
        return True
    if payload.get("missing_count", 0) == 0 and not payload.get("missing_coverage"):
        return True
    return False


def _render(gaps: list[dict[str, object]]) -> str:
    """Render the gap list into a bounded feedback block."""
    total = len(gaps)
    shown = gaps[:_MAX_GAPS]
    blocks = [_FEEDBACK_HEADER.format(n=total)]
    for i, gap in enumerate(shown, start=1):
        blocks.append(_gap_block(i, total, gap))
    if total > _MAX_GAPS:
        omitted = total - _MAX_GAPS
        blocks.append(f"... ({omitted} more gaps omitted, see verification_report.json)")
    return "\n\n".join(blocks)


def _gap_block(index: int, total: int, gap: dict[str, object]) -> str:
    """Render one gap entry as a labelled block."""
    return (
        f"--- Gap {index} of {total} ---\n"
        f"Path: {gap.get('navigation_path', '')}\n"
        f"Expected: {gap.get('expected_total', 0)} PDFs\n"
        f"Observed: {gap.get('observed_total', 0)} PDFs\n"
        f"Root cause: {gap.get('reason', '')}\n"
        f"Fix: {gap.get('step_0_fix', '')}"
    )
