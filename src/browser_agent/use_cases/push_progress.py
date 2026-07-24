"""Track elapsed + estimated remaining time for a Uwazi push.

Skipped rows count zero seconds: only active rows (create / update)
accumulate time, and the remaining estimate is the average active-row
time multiplied by the active rows still to go.
"""

from __future__ import annotations

import time


class PushProgress:
    """Track active-row elapsed time and estimate remaining push time."""

    def __init__(self, total_rows: int, total_active: int) -> None:
        self._total_rows: int = total_rows
        self._total_active: int = total_active
        self._active_elapsed: float = 0.0
        self._active_done: int = 0
        self._active_start: float | None = None

    def begin_active(self) -> None:
        """Start the timer for one active (non-skip) row."""
        self._active_start = time.monotonic()

    def end_active(self) -> None:
        """Stop the active-row timer and add the elapsed time to the total."""
        if self._active_start is None:
            return
        self._active_elapsed += time.monotonic() - self._active_start
        self._active_done += 1
        self._active_start = None

    def _remaining_seconds(self) -> float | None:
        """Estimate remaining seconds from average active-row time."""
        if self._active_done == 0 or self._total_active == 0:
            return None
        avg = self._active_elapsed / self._active_done
        return avg * (self._total_active - self._active_done)

    def format_prefix(self) -> str:
        """Return ``'Xm Ys elapsed ~ Xm remaining'`` for the progress log."""
        elapsed = _format_duration(self._active_elapsed)
        remaining = self._remaining_seconds()
        if remaining is None:
            return f"{elapsed} elapsed"
        return f"{elapsed} elapsed ~ {_format_duration(remaining)} remaining"


def _format_duration(seconds: float) -> str:
    """Format seconds as a compact human duration (``1m20s``, ``2h5m``, ``12s``)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"
