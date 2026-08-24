"""One row of the deterministic DB-vs-disk reconciliation.

Produced by :class:`ReconcileDownloadsUseCase` for every ``metadata.db``
row: the expected on-disk filename is recomputed from ``core_file_url``, the
file is stat-checked and validated, and both directions of the diff are
reported. This is the exhaustive inventory the LLM loop cannot produce.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReconciledPdf(BaseModel):
    """Deterministic verdict for one DB row + its on-disk file."""

    core_id: str = Field(default="", description="The DB row's core_id.")
    file_url: str = Field(default="", description="The core_file_url stored in the row data.")
    db_pdf_filename: str = Field(default="", description="The core_pdf_filename the step-0 LLM wrote.")
    expected_filename: str = Field(default="", description="Filename recomputed from core_file_url.")
    matched_filename: str = Field(default="", description="The filename that actually matched on disk.")
    match_mode: str = Field(
        default="none",
        description="How the file was found: normalized / original / missing.",
    )
    file_exists: bool = Field(default=False, description="True if any expected file is on disk.")
    file_size_bytes: int = Field(default=0, description="Size of the matched file (0 if missing).")
    is_valid_pdf: bool = Field(default=False, description="True if magic + EOF present.")
    is_suspiciously_small: bool = Field(default=False, description="True if valid but tiny.")
    filename_mismatch: bool = Field(
        default=False,
        description="True when db_pdf_filename differs from the computed name.",
    )
    verdict: str = Field(
        description="One of: present, file_not_downloaded, corrupt_file, " + "empty_pdf_url, suspiciously_small.",
    )
    notes: str = Field(default="", description="Human-readable detail.")
    download_status: str = Field(
        default="",
        description="The core_download_status stored in the row data ('downloaded', 'failed', or '' when the row has no download).",
    )
