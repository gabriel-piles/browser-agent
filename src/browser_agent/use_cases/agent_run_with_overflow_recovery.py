"""Shared helper: run a pydantic-ai agent with overflow-preserving retry.

When a reasoning model (deepseek-v4-flash) exhausts ``max_tokens`` on
thinking, pydantic-ai raises ``UnexpectedModelBehavior``. The standard
``agent.run()`` call discards the partial message history built during
the run, so a retry starts from scratch and loses all exploration context.

This helper uses ``agent.iter()`` instead, which exposes
``agent_run.all_messages()`` even when the run fails. On overflow we
truncate the captured history to the last few messages and retry with a
finalize directive, preserving the exploration context the model needs
to emit a correct structured result.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import UsageLimitExceeded

_FINALIZE_DIRECTIVE = (
    "\n\nIMPORTANT: your context is full. Emit your final structured result now without further tool calls."
)
_TRUNCATED_HISTORY_WINDOW = 6


async def run_agent_with_recovery(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None = None,
) -> Any:
    """Run ``agent`` via ``iter()`` and retry on overflow with partial history.

    On the first attempt we drive the agent through ``agent.iter()`` so that
    when a token-limit overflow occurs we still have access to the messages
    built so far (tool calls, exploration results). The retry truncates that
    history to the last few messages and appends a finalize directive so the
    model emits its structured result without further tool calls.
    """
    agent_run = None
    try:
        async with agent.iter(
            prompt,
            deps=deps,
            usage_limits=usage_limits,
            message_history=message_history,
        ) as run:
            agent_run = run
            async for _ in run:
                pass
        return run.result
    except (UnexpectedModelBehavior, UsageLimitExceeded):
        partial: list[Any] = list(agent_run.all_messages()) if agent_run else []
        return await _retry(agent, prompt, deps, usage_limits, message_history, partial)


async def _retry(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None,
    partial_messages: list[Any],
) -> Any:
    """Retry with a finalize directive using the best available history."""
    source: list[Any] = partial_messages if partial_messages else (message_history or [])
    if source:
        truncated: list[Any] = list(source[-_TRUNCATED_HISTORY_WINDOW:])
        return await agent.run(
            prompt + _FINALIZE_DIRECTIVE,
            deps=deps,
            usage_limits=usage_limits,
            message_history=truncated,
        )
    return await agent.run(
        prompt + _FINALIZE_DIRECTIVE,
        deps=deps,
        usage_limits=usage_limits,
    )
