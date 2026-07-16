"""Heuristic value-type guess for one metadata field.

Given a handful of sample strings scraped from ``metadata.db``, this
class best-guesses whether the field holds dates, numbers, or plain
strings. The guess feeds the :class:`MetadataFieldCatalog` the propose
LLM reads.
"""

from __future__ import annotations

from datetime import datetime


class MetadataValueTypeHeuristic:
    """Best-effort ``string`` | ``date`` | ``numeric`` guess for a field."""

    def infer(self, samples: list[str]) -> str:
        """Return the value type for ``samples`` (``string`` when empty)."""
        non_empty = [s for s in samples if s]
        if not non_empty:
            return "string"
        if all(self._looks_like_date(s) for s in non_empty):
            return "date"
        if all(self._looks_like_number(s) for s in non_empty):
            return "numeric"
        return "string"

    def _looks_like_date(self, value: str) -> bool:
        """True when ``value`` parses as an ISO date or numeric date."""
        if not value:
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except ValueError:
            pass
        parts = value.replace("/", "-").split("-")
        return len(parts) >= 2 and all(p.isdigit() for p in parts if p)

    def _looks_like_number(self, value: str) -> bool:
        """True when ``value`` parses as a number (commas tolerated)."""
        if not value:
            return False
        try:
            float(value.replace(",", ""))
            return True
        except ValueError:
            return False
