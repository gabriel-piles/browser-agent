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

from loguru import logger

from pydantic_ai import Agent, UnexpectedModelBehavior, UsageLimitExceeded, UserError as _UserError
from pydantic_ai.settings import ModelSettings
from browser_agent.agent_logging import record_llm_usage

_FINALIZE_DIRECTIVE = (
    "\n\nIMPORTANT: your context is full. Emit your final structured result now without further tool calls. "
    "Return ONLY a valid JSON object — no XML tags, no markdown fences, no prose before or after. "
    "The JSON must match the output schema exactly."
)
_TRUNCATED_HISTORY_WINDOW = 6


def _usage_of(holder: Any) -> Any:
    """Return the usage object whether ``usage`` is a property or a method."""
    usage = holder.usage
    return usage() if callable(usage) else usage


def _usage_counts(usage: Any) -> tuple[int, int, int]:
    """Extract ``(input_tokens, output_tokens, requests)`` with zero guards."""
    return (
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "requests", 0) or 0,
    )


def _failed_usage_counts(agent_run: Any) -> tuple[int, int, int]:
    """Usage of the overflowed attempt; zeros when unavailable."""
    if agent_run is None:
        return 0, 0, 0
    try:
        return _usage_counts(_usage_of(agent_run))
    except Exception:
        return 0, 0, 0


def _persist_transcript(agent_name: str, prompt: str, result: Any, usage: tuple[int, int, int]) -> None:
    try:
        from browser_agent.llm_transcript_logger import write_llm_transcript

        messages = list(result.all_messages()) if result is not None and hasattr(result, "all_messages") else []
        write_llm_transcript(
            agent_name,
            prompt,
            messages,
            {
                "input_tokens": usage[0],
                "output_tokens": usage[1],
                "requests": usage[2],
            },
        )
    except Exception:
        logger.exception("failed to persist {} transcript", agent_name)


async def run_agent_with_recovery(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None = None,
    agent_name: str = "agent",
    finalize_hint: str = "",
) -> Any:
    """Run ``agent`` via ``iter()`` and retry on overflow with partial history.

    On the first attempt we drive the agent through ``agent.iter()`` so that
    when a token-limit overflow occurs we still have access to the messages
    built so far (tool calls, exploration results). The retry truncates that
    history to the last few messages and appends a finalize directive so the
    model emits its structured result without further tool calls.

    ``finalize_hint`` appends agent-specific guidance to the retry prompt
    when the run had to be rescued from an overflow.
    """
    agent_run = None
    result = None
    failed = (0, 0, 0)
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
        failed = _failed_usage_counts(agent_run)
        result = await _retry(agent, prompt, deps, usage_limits, message_history, partial, finalize_hint)
    fin = _usage_counts(_usage_of(result))
    record_llm_usage(agent_name, failed[0] + fin[0], failed[1] + fin[1], failed[2] + fin[2])
    _persist_transcript(agent_name, prompt, result, (failed[0] + fin[0], failed[1] + fin[1], failed[2] + fin[2]))
    return result


def _finalize_prompt(prompt: str, finalize_hint: str) -> str:
    """Append the finalize directive plus any agent-specific honesty hint."""
    hint = f" {finalize_hint.strip()}" if finalize_hint.strip() else ""
    return prompt + _FINALIZE_DIRECTIVE + hint


async def _retry(
    agent: Agent,
    prompt: str,
    deps: Any,
    usage_limits: Any,
    message_history: list[Any] | None,
    partial_messages: list[Any],
    finalize_hint: str = "",
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
    final_prompt = _finalize_prompt(prompt, finalize_hint)
    # Disable thinking on the retry: the overflow happened because the model
    # spent the entire ``max_tokens`` budget on reasoning_content. The
    # finalize directive already has all context needed; ``thinking=False``
    # sends ``reasoning_effort='none'`` so the full budget goes to output.
    no_thinking: ModelSettings = {"thinking": False}
    source: list[Any] = partial_messages if partial_messages else (message_history or [])
    if source:
        truncated: list[Any] = _strip_unprocessed_tool_calls(list(source[-_TRUNCATED_HISTORY_WINDOW:]))
        if truncated:
            try:
                return await agent.run(
                    final_prompt,
                    deps=deps,
                    usage_limits=usage_limits,
                    message_history=truncated,
                    model_settings=no_thinking,
                )
            except _UserError:
                pass
    return await agent.run(
        final_prompt,
        deps=deps,
        usage_limits=usage_limits,
        model_settings=no_thinking,
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
