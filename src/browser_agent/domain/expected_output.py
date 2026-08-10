from __future__ import annotations

from pydantic import BaseModel, Field


class ExpectedOutput(BaseModel):
    """What the emitted script should produce for a scenario.

    The verifier checks three things: a minimum record count in
    ``metadata.db``, a set of fields that must be non-null in at
    least one row, and a minimum number of PDF files in the run's
    ``downloads/`` directory.
    """

    min_records: int = Field(
        default=1,
        description="Minimum number of save_record calls (rows in metadata.db).",
    )
    required_fields: list[str] = Field(
        default_factory=list,
        description="Field names that must be non-null in at least one row's data JSON.",
    )
    pdf_count: int = Field(
        default=0,
        description="Expected minimum number of PDF files in downloads/.",
    )
    description: str = Field(
        default="",
        description="Human-readable description of what correct output looks like.",
    )
