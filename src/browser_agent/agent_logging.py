"""Project-wide loguru helpers.

Anything that needs to be reused across the use case and the tools lives
here, so we don't have to worry about circular imports between the use
case (which registers the tools) and the tools (which need to log).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

# Shared loguru logger, bound to ``component="agent"``. The format string
# in :mod:`browser_agent.logging_config` shows the component as
# ``short_name`` and a tool name (if any) in a second column.
agent_logger = logger.bind(component="agent")

# Cumulative LLM token ledger (REAL pydantic-ai usage), shared across all
# agent calls so the whole run's actual LLM cost is visible. Module-level
# so it is circular-import-safe (imports only stdlib + loguru).
_LLM_INPUT_TOKENS = 0
_LLM_OUTPUT_TOKENS = 0
_LLM_CALLS = 0
_LLM_REQUESTS = 0


def reset_llm_estimates() -> None:
    """Zero the cumulative LLM token estimates. Once per driver run."""
    global _LLM_INPUT_TOKENS, _LLM_OUTPUT_TOKENS, _LLM_CALLS, _LLM_REQUESTS
    _LLM_INPUT_TOKENS = _LLM_OUTPUT_TOKENS = _LLM_CALLS = _LLM_REQUESTS = 0


def record_llm_usage(agent_name: str, input_tokens: int, output_tokens: int, requests: int = 0) -> None:
    """Add one agent call's REAL pydantic-ai usage and log the running totals."""
    global _LLM_INPUT_TOKENS, _LLM_OUTPUT_TOKENS, _LLM_CALLS, _LLM_REQUESTS
    _LLM_CALLS += 1
    _LLM_REQUESTS += requests
    _LLM_INPUT_TOKENS += input_tokens
    _LLM_OUTPUT_TOKENS += output_tokens
    agent_logger.bind(agent=agent_name).info(
        "LLM   usage call={n} req={r} in_tok={i} out_tok={o} | run total in={ti} out={to}",
        n=_LLM_CALLS,
        r=_LLM_REQUESTS,
        i=input_tokens,
        o=output_tokens,
        ti=_LLM_INPUT_TOKENS,
        to=_LLM_OUTPUT_TOKENS,
    )


def log_llm_total_summary() -> None:
    """Log the final cumulative real-usage block at end of run."""
    agent_logger.info(
        "LLM   FINAL USAGE calls={c} requests={r} in_tokens={i} out_tokens={o}",
        c=_LLM_CALLS,
        r=_LLM_REQUESTS,
        i=_LLM_INPUT_TOKENS,
        o=_LLM_OUTPUT_TOKENS,
    )


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
