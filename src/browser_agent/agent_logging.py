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


@asynccontextmanager
async def traced_tool(name: str) -> AsyncIterator[None]:
    """Async context manager that logs the start, end and duration of a tool.

    Wrap any tool body with this to get consistent timing/exception lines
    in the same format as the orchestrator's own messages::

        async def my_tool(ctx, ...):
            async with traced_tool("my_tool"):
                ...
    """
    started = time.monotonic()
    agent_logger.bind(tool=name).info("TOOL   start")
    try:
        yield
    except Exception:
        agent_logger.bind(tool=name).exception(
            "TOOL   FAILED elapsed={elapsed:.1f}s",
            elapsed=time.monotonic() - started,
        )
        raise
    else:
        agent_logger.bind(tool=name).info(
            "TOOL   done   elapsed={elapsed:.1f}s",
            elapsed=time.monotonic() - started,
        )
