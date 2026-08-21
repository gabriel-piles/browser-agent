"""Project-wide loguru helpers.

Anything that needs to be reused across the use case and the tools lives
here, so we don't have to worry about circular imports between the use
case (which registers the tools) and the tools (which need to log).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger

# Shared loguru logger, bound to ``component="agent"``. The format string
# in :mod:`browser_agent.logging_config` shows the component as
# ``short_name`` and a tool name (if any) in a second column.
agent_logger = logger.bind(component="agent")

# Cumulative LLM token ledger (estimated as letters ÷ 4), shared across all
# agent calls so the whole run's approximate LLM cost is visible. Module-level
# so it is circular-import-safe (imports only stdlib + loguru).
_LLM_INPUT_TOKENS = 0
_LLM_OUTPUT_TOKENS = 0
_LLM_CALLS = 0


def reset_llm_estimates() -> None:
    """Zero the cumulative LLM token estimates. Once per driver run."""
    global _LLM_INPUT_TOKENS, _LLM_OUTPUT_TOKENS, _LLM_CALLS
    _LLM_INPUT_TOKENS = _LLM_OUTPUT_TOKENS = _LLM_CALLS = 0


def record_llm_estimate(agent_name: str, input_letters: int, output_letters: int) -> None:
    """Add one agent call's estimated tokens and log the running totals."""
    global _LLM_INPUT_TOKENS, _LLM_OUTPUT_TOKENS, _LLM_CALLS
    _LLM_CALLS += 1
    _LLM_INPUT_TOKENS += input_letters // 4
    _LLM_OUTPUT_TOKENS += output_letters // 4
    agent_logger.bind(agent=agent_name).info(
        "LLM   call={n} in_tok={i} out_tok={o} | run total in={ti} out={to} total={tt}",
        n=_LLM_CALLS,
        i=input_letters // 4,
        o=output_letters // 4,
        ti=_LLM_INPUT_TOKENS,
        to=_LLM_OUTPUT_TOKENS,
        tt=_LLM_INPUT_TOKENS + _LLM_OUTPUT_TOKENS,
    )


def record_llm_usage(agent_name: str, input_tokens: int, output_tokens: int) -> None:
    """Add one agent call's REAL pydantic-ai usage to the same ledger.

    Complements :func:`record_llm_estimate`: the pre-run estimate only sees
    the initial prompt, so after each run we accrue the actual per-run
    usage (system prompt, tool returns, thinking included) so totals match
    pydantic-ai's USAGE lines.
    """
    global _LLM_INPUT_TOKENS, _LLM_OUTPUT_TOKENS
    _LLM_INPUT_TOKENS += input_tokens
    _LLM_OUTPUT_TOKENS += output_tokens
    agent_logger.bind(agent=agent_name).info(
        "LLM   usage in_tok={i} out_tok={o} | run total in={ti} out={to} total={tt}",
        i=input_tokens,
        o=output_tokens,
        ti=_LLM_INPUT_TOKENS,
        to=_LLM_OUTPUT_TOKENS,
        tt=_LLM_INPUT_TOKENS + _LLM_OUTPUT_TOKENS,
    )


def log_llm_total_summary() -> None:
    """Log the final cumulative estimate block at end of run."""
    agent_logger.info(
        "LLM   FINAL ESTIMATES calls={c} in_tokens={i} out_tokens={o} total_tokens={t}",
        c=_LLM_CALLS,
        i=_LLM_INPUT_TOKENS,
        o=_LLM_OUTPUT_TOKENS,
        t=_LLM_INPUT_TOKENS + _LLM_OUTPUT_TOKENS,
    )


def estimate_output_letters(output: Any) -> int:
    """Estimated received-token letters for a pydantic-ai run's structured output.

    Empty/None → 0; a str uses its length; a pydantic model uses its JSON
    length; anything else uses str(). Estimated output letters.
    """
    if output is None:
        return 0
    if isinstance(output, str):
        return len(output)
    if hasattr(output, "model_dump_json"):
        return len(output.model_dump_json())
    return len(str(output))


@asynccontextmanager
async def traced_tool(name: str, *, summary: str = "") -> AsyncIterator[None]:
    """Async context manager that logs the start, end and duration of a tool.

    Wrap any tool body with this to get consistent timing/exception lines
    in the same format as the orchestrator's own messages::

        async def my_tool(ctx, ...):
            async with traced_tool("my_tool"):
                ...

    When *summary* is given it is appended to the log line (e.g. the
    :class:`PageAction` the LLM asked the browser to perform).
    """
    started = time.monotonic()
    suffix = f"   {summary}" if summary else ""
    agent_logger.bind(tool=name).info(f"TOOL   start{suffix}")
    try:
        yield
    except Exception:
        agent_logger.bind(tool=name).exception(
            f"TOOL   FAILED elapsed={time.monotonic() - started:.1f}s{suffix}",
        )
        raise
    else:
        agent_logger.bind(tool=name).info(
            f"TOOL   done   elapsed={time.monotonic() - started:.1f}s{suffix}",
        )
