"""Outcome of verifying one source_url against a run's ``metadata.db``.

``ProbeVerdict`` is the enum; ``ProbeResult`` carries the per-URL
details. The verifier produces one result per URL; the driver merges
the list into ``VerificationReport.probe_results``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ProbeVerdict(str, Enum):
    """The deterministic outcome of checking one source_url."""

    CAPTURED = "captured"
    MISSING_URL = "missing_url"


class ProbeResult(BaseModel):
    """One source_url's deterministic verification outcome."""

    source_url: str = Field(description="The source_url under verification.")
    verdict: ProbeVerdict = Field(description="The deterministic verdict for this URL.")
    matched_row_source_url: str = Field(
        default="",
        description="The metadata.db row source_url that matched, or '' when none.",
    )
