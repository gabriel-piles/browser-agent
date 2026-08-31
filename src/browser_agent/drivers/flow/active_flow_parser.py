"""Parse the ``active_flow`` selector from ``data/active_run.yaml``.

The selector names the split folders (created by step 0) this step-1
invocation must run: numbers or ranges separated by commas, e.g.
``2-5,6,125-455``. Each number is a split folder's order prefix (the
leading ``N_`` of ``N_short_name``); ranges are inclusive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE_TOKEN = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?$")
_MAX_SPAN = 10_000


class ActiveFlowError(ValueError):
    """Raised when the active_flow selector cannot be parsed."""


@dataclass(frozen=True)
class ActiveFlowSelection:
    """The parsed set of split order numbers to run, plus the raw text."""

    orders: frozenset[int]
    raw: str

    def describe(self) -> str:
        """Compact ranges/numbers text (e.g. ``2-5,6``) for logging."""
        return format_orders(sorted(self.orders))


def parse_active_flow(raw: str) -> ActiveFlowSelection:
    """Parse ``raw`` (e.g. ``"2-5,6,125-455"``) into the set of orders to run.

    Empty/whitespace tokens are rejected loudly — a silently empty
    selector would run nothing and look like success.
    """
    text = (raw or "").strip()
    if not text:
        raise ActiveFlowError(
            "active_run.yaml must set 'active_flow' (e.g. '2-5,6,125-455') — step 1 needs to know which splits to run"
        )
    orders: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            raise ActiveFlowError(f"active_flow has an empty token: {raw!r}")
        match = _RANGE_TOKEN.match(token)
        if match is None:
            raise ActiveFlowError(f"active_flow token {token!r} is not a number or N-M range (raw={raw!r})")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) is not None else start
        if end < start:
            raise ActiveFlowError(f"active_flow range {token!r} is inverted (raw={raw!r})")
        if end - start > _MAX_SPAN:
            raise ActiveFlowError(f"active_flow range {token!r} spans more than {_MAX_SPAN} splits (raw={raw!r})")
        orders.update(range(start, end + 1))
    return ActiveFlowSelection(orders=frozenset(orders), raw=text)


def format_orders(orders: list[int]) -> str:
    """Render sorted orders back into compact comma text (``1,2-5,9``)."""
    if not orders:
        return ""
    sorted_orders = sorted(set(orders))
    parts: list[str] = []
    run_start = sorted_orders[0]
    prev = sorted_orders[0]
    for value in sorted_orders[1:]:
        if value == prev + 1:
            prev = value
            continue
        parts.append(f"{run_start}-{prev}" if prev > run_start else str(run_start))
        run_start = prev = value
    parts.append(f"{run_start}-{prev}" if prev > run_start else str(run_start))
    return ",".join(parts)
