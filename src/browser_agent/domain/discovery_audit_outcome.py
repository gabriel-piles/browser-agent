"""The result of an independent discovery audit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DiscoveryAuditOutcome(BaseModel):
    """Outcome of an independent audit re-walk.

    ``skipped``: could not parse the manifest or no targets; ``passed``:
    coverage + independent counts match; ``discrepancies``: report holds
    the discrepancy blocks.
    """

    status: Literal["skipped", "passed", "discrepancies"]
    report: str = ""
    reason: str = ""
