"""Structured output of the download-verification agent."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.missing_coverage import MissingCoverage
from browser_agent.domain.pdf_check_result import PdfCheckResult


class VerificationReport(BaseModel):
    """Aggregated findings of the verification agent.

    Tells the operator which PDFs are present, missing, or corrupt, and
    for each prompt-described path that is not fully covered, a concrete
    step-0 fix the operator can feed back to the scraping agent.
    """

    overall_assessment: str = Field(
        description="A 2-3 sentence summary measured against the original prompt.",
    )
    pdf_results: list[PdfCheckResult] = Field(
        default_factory=list,
        description="Per-PDF verification outcomes from check_pdf.",
    )
    missing_count: int = Field(
        default=0,
        description="Count of results whose verdict is not 'present'.",
    )
    missing_coverage: list[MissingCoverage] = Field(
        default_factory=list,
        description="One entry per prompt-described path not fully covered.",
    )
    recommendations: str = Field(
        description="Short step-0 handoff summary referencing missing_coverage.",
    )
