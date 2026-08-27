"""Structured reply from the constrained script-adaptation LLM call."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScriptReuseDecision(BaseModel):
    """One constrained adapt-or-reject judgment over a sibling script.

    The model either returns the source script with only its constants
    changed for the target subtask, or rejects the source as unusable
    for the target's page types.
    """

    status: Literal["adapted", "incompatible"] = Field(
        description=(
            '"adapted" when the source script satisfies the target subtask '
            'with only constant changes; "incompatible" when the source\'s '
            "page type or mechanics cannot serve the target"
        ),
    )
    python_code: str = Field(
        default="",
        description=("The adapted script source. REQUIRED when status is 'adapted'; empty when 'incompatible'"),
    )
    explanation: str = Field(
        default="",
        description="What constants were changed, or why the source is incompatible",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Pip packages required by python_code (copied from the source script)",
    )
    pdf_download_strategy: str = Field(
        default="browser_fetch",
        description="Copied from the source script — reuse the proven strategy",
    )
    kind: str = Field(
        default="processing",
        description='Script kind: "discovery" or "processing" (same as the source)',
    )
