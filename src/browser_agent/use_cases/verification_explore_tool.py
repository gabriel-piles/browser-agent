"""Typed ``explore_page`` wrapper for the verification agent.

``explore_page`` in :mod:`explore_page_tool` is typed
``RunContext[AgentDeps]``. The verification agent uses
:class:`VerificationAgentDeps`. This thin adapter reuses all the
formatting logic while satisfying the type checker.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.page_action import PageAction
from browser_agent.use_cases.explore_page_tool import _action_summary, _format_snapshot
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps


async def explore_page(ctx: RunContext[VerificationAgentDeps], action: PageAction) -> str:
    """Perform ``action`` in the persistent browser tab and return the result.

    Identical body to the step 0 ``explore_page`` — the only difference
    is the ``RunContext`` deps type. ``VerificationAgentDeps.browser_session``
    is the same :class:`BrowserSessionPort` type. Use for NAVIGATION/DISCOVERY
    only — never to fetch a PDF.
    """
    session = ctx.deps.browser_session
    summary = _action_summary(action)
    async with traced_tool("explore_page", summary=summary):
        snapshot = await session.perform(action)
    return _format_snapshot(snapshot)
