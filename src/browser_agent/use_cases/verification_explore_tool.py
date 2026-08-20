"""Typed ``explore_page`` wrapper for the verification agent.

``explore_page`` in :mod:`explore_page_tool` is typed
``RunContext[AgentDeps]``. The verification agent uses
:class:`VerificationAgentDeps`. This thin adapter reuses all the
formatting logic while satisfying the type checker.
"""

from __future__ import annotations

from loguru import logger
from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.configuration import MAX_EMPTY_EXPLORE_RESULTS
from browser_agent.domain.page_action import PageAction
from browser_agent.domain.page_snapshot import PageSnapshot
from browser_agent.use_cases.explore_duplicate_guard import action_key, suppression_message
from browser_agent.use_cases.explore_page_tool import (
    _action_summary,
    _budget_footer,
    _empty_result_directive,
    _empty_result_hint,
    _explore_limit_reached,
    _format_snapshot,
    _is_empty_result,
)
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps


async def explore_page(ctx: RunContext[VerificationAgentDeps], action: PageAction) -> str:
    """Perform ``action`` in the persistent browser tab and return the result.

    Identical body to the step 0 ``explore_page`` — the only difference
    is the ``RunContext`` deps type. ``VerificationAgentDeps.browser_session``
    is the same :class:`BrowserSessionPort` type. Use for NAVIGATION/DISCOVERY
    only — never to fetch a PDF.

    Budget enforcement is shared with step 0: explore calls are capped at
    ``deps.explore_limit`` and three consecutive empty results return a
    hard ``# STOP`` directive, so a dead selector cannot loop the model
    into an unbounded prompt.
    """
    deps = ctx.deps
    deps.explore_calls += 1
    if deps.explore_calls > deps.explore_limit:
        return _explore_limit_reached(deps)
    session = deps.browser_session
    summary = _action_summary(action)
    key = action_key(action)
    if deps.explore_guard.check(key):
        deps.explore_guard.suppressed += 1
        return suppression_message()
    deps.explore_guard.remember(key)
    async with traced_tool("explore_page", summary=summary):
        snapshot: PageSnapshot = await session.perform(action)
    if snapshot.error:
        logger.warning(
            "explore_page ERROR — {action}: {error}",
            action=summary,
            error=snapshot.error,
        )
    empty = _is_empty_result(action, snapshot)
    if empty:
        deps.empty_result_streak += 1
    else:
        deps.empty_result_streak = 0
    if action.action == "analyze" and snapshot.structure is not None:
        deps.last_analyze_selectors = [p.selector for p in snapshot.structure.link_patterns]
    if deps.empty_result_streak >= MAX_EMPTY_EXPLORE_RESULTS:
        return _empty_result_directive(deps)
    result = _format_snapshot(snapshot)
    if empty:
        result += _empty_result_hint(deps)
    return result + _budget_footer(deps)
