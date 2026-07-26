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
