"""Integrity verdict for one downloaded PDF file.

Separates *invalid* (magic / EOF failed) from *suspiciously small*
(size outlier but structurally fine) so the report can distinguish
real corruption from a small-but-legitimate file.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PdfIntegrityResult(BaseModel):
    """Structural integrity of a single on-disk PDF."""

    file_size: int = Field(default=0, description="Bytes on disk (0 if missing).")
    has_pdf_magic: bool = Field(
        default=False,
        description="True if the file starts with the ``%PDF`` magic bytes.",
    )
    has_eof_marker: bool = Field(
        default=False,
        description="True if ``%%EOF`` appears in the last ~2 KB of the file.",
    )
    is_valid: bool = Field(
        default=False,
        description="True when magic AND EOF are present (size-independent).",
    )
    is_suspiciously_small: bool = Field(
        default=False,
        description="True when valid but below the suspicious-size threshold.",
    )
    notes: str = Field(default="", description="Human-readable integrity detail.")
