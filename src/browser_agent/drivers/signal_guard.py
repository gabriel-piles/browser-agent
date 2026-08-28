"""Clean, logged, resumable stop on SIGTERM/SIGHUP/SIGINT.

Installs asyncio signal handlers that cancel the running flow task so
the driver's ``finally`` (heartbeat stop, Chromium kill, profile delete)
runs and persisted state stays resumable.
"""

from __future__ import annotations

import asyncio
import signal

from loguru import logger

_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
_EXIT_CODES = {signal.SIGTERM: 143, signal.SIGHUP: 129, signal.SIGINT: 130}


class SignalGuard:
    """Cancel the flow on the first received signal; report the exit code."""

    def __init__(self) -> None:
        self._received: int | None = None
        self._task: asyncio.Task | None = None

    def install(self) -> None:
        loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        for sig in _SIGNALS:
            loop.add_signal_handler(sig, self._on_signal, sig)

    def _on_signal(self, sig: int) -> None:
        if self._received is not None:
            return
        self._received = sig
        name = _name_for(sig)
        logger.warning(
            "received signal {name} - cancelling flow; state persists, rerun the same command to resume",
            name=name,
        )
        if self._task is not None:
            self._task.cancel()

    def signal_name(self) -> str:
        return _name_for(self._received) if self._received is not None else "NONE"

    def exit_code(self) -> int:
        return _EXIT_CODES.get(self._received, 2) if self._received is not None else 2

    def uninstall(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in _SIGNALS:
            try:
                loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass


def _name_for(sig: int) -> str:
    return signal.Signals(sig).name
