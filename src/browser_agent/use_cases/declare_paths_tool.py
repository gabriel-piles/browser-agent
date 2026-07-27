"""The ``declare_paths`` tool bound to the verification agent.

Makes the agent's prompt→path decomposition an explicit, auditable
artifact instead of a silent mental model. The agent MUST call this
first, listing every navigation path/filter/page the prompt says
yields PDFs. The declared paths are stored on
:class:`VerificationAgentDeps` and echoed (remaining unvisited ones) in
every subsequent tool return so the agent cannot silently truncate its
coverage checklist.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.expected_path import ExpectedPath
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps


async def declare_paths(ctx: RunContext[VerificationAgentDeps], paths: list[ExpectedPath]) -> str:
    """Record the prompt-described paths the agent commits to checking.

    Call this FIRST, before any exploration. List every navigation
    path/filter/page the Original Task prompt says yields PDFs. Each
    subsequent ``check_pdf`` return echoes the paths still unvisited so
    nothing is silently dropped.
    """
    deps = ctx.deps
    async with traced_tool("declare_paths", summary=f"{len(paths)} paths"):
        deps.declared_paths = list(paths)
    return _render(deps)


def remaining_paths_block(deps: VerificationAgentDeps) -> str:
    """Return the unvisited-paths footer appended to later tool returns."""
    if not deps.declared_paths:
        return ""
    unvisited = [p.path for p in deps.declared_paths if not p.visited]
    if not unvisited:
        return "\n# All declared paths visited."
    body = "\n".join(f"  - {p}" for p in unvisited)
    return f"\n# Remaining unvisited declared paths ({len(unvisited)}):\n{body}"


def _render(deps: VerificationAgentDeps) -> str:
    lines = [f"# declare_paths: recorded {len(deps.declared_paths)} path(s)"]
    for p in deps.declared_paths:
        lines.append(f"  - {p.path}" + (f" ({p.expected_count_hint})" if p.expected_count_hint else ""))
    lines.append(
        "Check every declared path. Each subsequent check_pdf return " + "echoes the paths still unvisited.",
    )
    return "\n".join(lines)
