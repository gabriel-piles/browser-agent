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
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError as _UserError
from pydantic_ai.usage import UsageLimitExceeded

from browser_agent.agent_logging import estimate_output_letters, record_llm_estimate, record_llm_usage

_FINALIZE_DIRECTIVE = (
    "\n\nIMPORTANT: your context is full. Emit your final structured result now without further tool calls. "
    "Return ONLY a valid JSON object — no XML tags, no markdown fences, no prose before or after. "
    "The JSON must match the output schema exactly."
)
_TRUNCATED_HISTORY_WINDOW = 6


async def run_agent_with_recovery(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None = None,
    agent_name: str = "agent",
) -> Any:
    """Run ``agent`` via ``iter()`` and retry on overflow with partial history.

    On the first attempt we drive the agent through ``agent.iter()`` so that
    when a token-limit overflow occurs we still have access to the messages
    built so far (tool calls, exploration results). The retry truncates that
    history to the last few messages and appends a finalize directive so the
    model emits its structured result without further tool calls.
    """
    agent_run = None
    result = None
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
        result = run.result
    except (UnexpectedModelBehavior, UsageLimitExceeded):
        partial: list[Any] = list(agent_run.all_messages()) if agent_run else []
        result = await _retry(agent, prompt, deps, usage_limits, message_history, partial)
    record_llm_estimate(agent_name, len(prompt), estimate_output_letters(getattr(result, "output", None)))
    usage = getattr(result, "usage", None)
    if callable(usage):
        real = usage()
        record_llm_usage(
            agent_name,
            getattr(real, "input_tokens", 0) or 0,
            getattr(real, "output_tokens", 0) or 0,
        )
    return result


async def _retry(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None,
    partial_messages: list[Any],
) -> Any:
    """Retry with a finalize directive using the best available history.

    The partial messages captured from a failed ``agent.iter()`` run may
    contain ``ModelResponse`` entries with unprocessed tool calls (the
    agent was interrupted mid-tool-call). Passing those to ``agent.run()``
    raises ``UserError: Cannot provide a new user prompt when the message
    history contains unprocessed tool calls.`` We strip any trailing
    ``ModelResponse`` that contains a ``ToolCallPart`` so the retry starts
    from a clean, balanced message history.
    """
    source: list[Any] = partial_messages if partial_messages else (message_history or [])
    if source:
        truncated: list[Any] = _strip_unprocessed_tool_calls(list(source[-_TRUNCATED_HISTORY_WINDOW:]))
        if truncated:
            try:
                return await agent.run(
                    prompt + _FINALIZE_DIRECTIVE,
                    deps=deps,
                    usage_limits=usage_limits,
                    message_history=truncated,
                )
            except _UserError:
                pass
    return await agent.run(
        prompt + _FINALIZE_DIRECTIVE,
        deps=deps,
        usage_limits=usage_limits,
    )


def _strip_unprocessed_tool_calls(messages: list[Any]) -> list[Any]:
    """Remove trailing ``ModelResponse`` entries that contain unprocessed tool calls.

    A ``ModelResponse`` with ``ToolCallPart`` entries requires matching
    ``ToolReturnPart`` messages in the history. When the agent was
    interrupted mid-run, those returns are absent. We scan backwards and
    drop any trailing ``ModelResponse`` whose last part is a tool call,
    so the remaining history is balanced and ``agent.run()`` accepts it.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    while messages:
        last = messages[-1]
        if isinstance(last, ModelResponse) and any(isinstance(p, ToolCallPart) for p in last.parts):
            messages.pop()
            continue
        break
    return messages
