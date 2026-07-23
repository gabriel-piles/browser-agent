from __future__ import annotations

from pydantic import BaseModel, Field


class PdfCheckResult(BaseModel):
    """Per-PDF validation outcome from the ``check_pdf`` tool.

    Captures whether the PDF URL is present in ``metadata.db``,
    whether the downloaded file exists on disk, and whether the file
    is a valid PDF (magic bytes ``%PDF`` and size > 1 KB).
    """

    url: str = Field(description="The PDF URL that was checked.")
    found_in_db: bool = Field(description="True if a matching row exists in metadata.db.")
    db_source_url: str = Field(default="", description="The source_url of the matching DB row.")
    pdf_filename: str = Field(default="", description="The pdf_filename from the DB row data.")
    file_exists: bool = Field(default=False, description="True if the file exists on disk.")
    file_size_bytes: int = Field(default=0, description="Size of the file in bytes (0 if missing).")
    is_valid_pdf: bool = Field(
        default=False,
        description="True if the file starts with %PDF and is larger than 1 KB.",
    )
    verdict: str = Field(
        description="One of: present, missing_from_db, file_not_downloaded, corrupt_file.",
    )
    notes: str = Field(default="", description="Extra context from the check.")
