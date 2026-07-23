"""Typed ``explore_page`` wrapper for the validation agent.

``explore_page`` in :mod:`explore_page_tool` is typed
``RunContext[AgentDeps]``. The validation agent uses
:class:`ValidationAgentDeps`. This thin adapter reuses all the
formatting logic while satisfying the type checker.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.page_action import PageAction
from browser_agent.use_cases.explore_page_tool import _action_summary, _format_snapshot
from browser_agent.use_cases.validation_agent_deps import ValidationAgentDeps


async def explore_page(ctx: RunContext[ValidationAgentDeps], action: PageAction) -> str:
    """Perform ``action`` in the persistent browser tab and return the result.

    Identical body to the step 0 ``explore_page`` — the only difference
    is the ``RunContext`` deps type. ``ValidationAgentDeps.browser_session``
    is the same :class:`BrowserSessionPort` type.
    """
    session = ctx.deps.browser_session
    summary = _action_summary(action)
    async with traced_tool("explore_page", summary=summary):
        snapshot = await session.perform(action)
    return _format_snapshot(snapshot)
