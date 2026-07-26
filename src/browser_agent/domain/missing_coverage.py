"""One prompt-described path the scraper failed to fully cover."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MissingCoverage(BaseModel):
    """A prompt-described path/filter/page the scraper missed or mishandled.

    Captures the gap between what the Original Task prompt required and
    what the run actually produced, plus a concrete instruction to hand
    to the step 0 agent so it can fix the divergence.
    """

    navigation_path: str = Field(
        description="The prompt-described path/filter/page that was missed.",
    )
    expected: str = Field(
        description="What the prompt says should be there.",
    )
    actual: str = Field(
        description="What was found: none / partial / corrupt (+ counts).",
    )
    reason: str = Field(
        description="Why the scraper missed it (logic bug, wrong selector, ...).",
    )
    step_0_fix: str = Field(
        description="Concrete instruction to pass to the step 0 agent.",
    )
