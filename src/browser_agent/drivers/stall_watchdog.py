"""C-level stack dump when the event loop freezes.

Uses :mod:`faulthandler` to dump all thread stacks after a fixed delay.
The heartbeat re-arms the timer on every tick; a frozen loop stops
re-arming and the stacks land in ``logs/stall_dump.log``.
"""

from __future__ import annotations

import faulthandler
import typing
from pathlib import Path

_STALL_DUMP_S = 900.0


class StallWatchdog:
    """Arm a faulthandler traceback dump on a fixed stall interval."""

    def __init__(self) -> None:
        self._file: typing.Any = None

    def attach(self, log_path: Path) -> None:
        self._file = open(log_path, "a", encoding="utf-8")

    def arm(self) -> None:
        if self._file is None:
            return
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(_STALL_DUMP_S, exit=False, file=self._file)

    def disarm(self) -> None:
        faulthandler.cancel_dump_traceback_later()

    def close(self) -> None:
        faulthandler.cancel_dump_traceback_later()
        if self._file is not None:
            self._file.close()
            self._file = None
