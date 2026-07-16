from __future__ import annotations

from pydantic import BaseModel


class DownloadResult(BaseModel):
    """Metadata result of a PDF download attempt.

    ``skipped`` is True when the target file already exists on disk
    and the download was short-circuited. ``reason`` carries a
    short machine-friendly token (``"already_downloaded"`` /
    ``"downloaded"`` / ``"empty_existing"`` etc.) for log triage.
    """

    success: bool
    saved_path: str
    url: str
    content_type: str
    file_size_bytes: int
    error: str = ""
    skipped: bool = False
    reason: str = ""
