from __future__ import annotations

from pydantic import BaseModel


class DownloadResult(BaseModel):
    """Metadata result of a PDF download attempt."""

    success: bool
    saved_path: str
    url: str
    content_type: str
    file_size_bytes: int
    error: str = ""
