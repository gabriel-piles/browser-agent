from __future__ import annotations

from pydantic import BaseModel, Field


class PdfCheckRequest(BaseModel):
    """Parameters for the ``check_pdf`` validation tool.

    - ``url`` — the PDF URL the agent found during exploration.
    - ``navigation_path`` — human-readable steps the agent took to find it.
    - ``notes`` — why the agent thinks this PDF might be missing.
    """

    url: str = Field(description="The PDF URL the agent found during exploration.")
    navigation_path: str = Field(
        description="Human-readable steps the agent took to find this PDF.",
    )
    notes: str = Field(default="", description="Why this PDF might be missing.")
