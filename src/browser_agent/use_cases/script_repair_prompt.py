"""Format lint findings and smoke-test failures as agent repair prompts.

The driver feeds these back to the generation agent as a follow-up
user message (with the prior message history) so the model can fix
the exact bug class without consuming a validation attempt. The
attempt budget pays for browser work, not for syntax.
"""

from __future__ import annotations

from browser_agent.domain.lint_finding import LintFinding

_LINT_HEADER = (
    "The script you emitted has deterministic lint violations "
    "(checked by a pure-Python linter, NOT a validation run — this "
    "does NOT consume a validation attempt). Fix every violation "
    "below and emit the corrected GeneratedScript.\n\n"
)
_SMOKE_HEADER = (
    "The final emitted script FAILED the smoke test (a 60-second "
    "subprocess run of the EXACT file the operator will run). This "
    "is NOT a validation attempt — it does NOT consume one. Fix the "
    "crash below and emit the corrected GeneratedScript.\n\n"
)
_DISCOVERY_HEADER = (
    "An independent verification script re-walked the site and proved your "
    "emitted script's LINK DISCOVERY is INCOMPLETE: it under-collects target "
    "links on the paths listed below (e.g. it stops at page one). This is NOT "
    "a validation attempt — it does NOT consume one. Fix the discovery loop "
    "(scroll / load-more trigger / dropdown iteration) so it reaches the "
    "site-advertised totals on every path, keeping the rest of the pipeline "
    "intact, and emit the corrected GeneratedScript.\n\n"
)


def format_lint_repair(findings: list[LintFinding]) -> str:
    """Format lint findings as a repair prompt for the agent."""
    lines = [_LINT_HEADER]
    for f in findings:
        loc = f" (line {f.line})" if f.line is not None else ""
        lines.append(f"- rule {f.rule} [{f.severity}]{loc}: {f.message}")
    lines.append("\nEmit the full corrected GeneratedScript now.")
    return "\n".join(lines)


def format_smoke_repair(output: str) -> str:
    """Format a smoke-test failure output as a repair prompt."""
    return f"{_SMOKE_HEADER}```\n{output}\n```\n\nEmit the full corrected GeneratedScript now."


def format_discovery_repair(report: str) -> str:
    """Format a discovery-verification report as a repair prompt."""
    return f"{_DISCOVERY_HEADER}```\n{report}\n```\n\nEmit the full corrected GeneratedScript now."


def format_processing_self_check_repair(output: str, violations: list[str] | None = None) -> str:
    """Format a processing self-check failure as a repair prompt."""
    header = (
        "The emitted processing script FAILED the processing self-check (it ran "
        "against the sample document links and either downloaded zero files or "
        "violated correctness invariants). This is NOT a validation attempt. "
        "Fix the download + save_record path and the invariants below, then emit "
        "the corrected GeneratedScript."
    )
    parts = [header]
    if violations:
        parts.append("\n\nCorrectness violations:\n")
        parts.append("\n".join(f"- {v}" for v in violations))
    parts.append(f"\n\n```\n{output}\n```\n\nEmit the full corrected GeneratedScript now.")
    return "".join(parts)
