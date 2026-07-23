from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.pdf_check_result import PdfCheckResult


class ValidationReport(BaseModel):
    """Structured output of the validation agent.

    Aggregates per-PDF check results into a summary the operator can
    act on: which PDFs are missing, corrupt, or present, plus
    recommendations for fixing the scraper.
    """

    overall_assessment: str = Field(
        description="A 2-3 sentence summary of the scraping quality.",
    )
    pdf_results: list[PdfCheckResult] = Field(
        default_factory=list,
        description="Per-PDF validation outcomes.",
    )
    missing_count: int = Field(
        default=0,
        description="Count of results whose verdict is not 'present'.",
    )
    recommendations: str = Field(
        description="What the operator should fix in the scraper.",
    )
