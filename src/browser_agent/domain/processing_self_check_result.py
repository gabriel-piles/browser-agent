from __future__ import annotations

from pydantic import BaseModel, Field


class ProcessingSelfCheckResult(BaseModel):
    """Outcome of running the emitted processing script against sample links.

    The self-check seeds the scratch DB with the explorer's
    ``sample_document_urls`` as ``status='discovered'`` links, runs the
    processing script, and counts rows whose ``download_status == "downloaded"``
    with a non-empty ``pdf_filename`` or ``supporting_filename``. ``success``
    is true when at least one file was downloaded and saved and no
    correctness violations remain — proving the download + ``save_record``
    path works before the script is delivered.
    """

    success: bool
    downloaded_rows: int
    record_count: int
    output: str
    violations: list[str] = Field(default_factory=list)
