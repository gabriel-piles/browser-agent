"""Periodic elapsed-time heartbeat for long scraping flows.

Logs how long the flow driver has been running every five minutes so the
user can gauge progress without scrolling back to the "flow driver starting"
line. Reuses the standard loguru stderr sink.
"""

from __future__ import annotations

import asyncio
import time

from loguru import logger

_INTERVAL_SECONDS = 300
_START_MESSAGE = "flow driver still running elapsed={elapsed}"
_STOP_MESSAGE = "flow driver finished elapsed={elapsed}"


class RunElapsedHeartbeat:
    """Logs elapsed run time on a fixed interval until stopped."""

    def __init__(self) -> None:
        self._started: float = time.monotonic()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(_INTERVAL_SECONDS)
            logger.info(_START_MESSAGE, elapsed=self._format_elapsed())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        logger.info(_STOP_MESSAGE, elapsed=self._format_elapsed())

    def _format_elapsed(self) -> str:
        secs = int(time.monotonic() - self._started)
        if secs < 3600:
            return f"{secs // 60}m"
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
