"""One metadata row whose PDF failed to download or is missing from disk."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FailedDocument(BaseModel):
    """A retryable download gap surfaced by a refresh pass."""

    core_id: str
    file_url: str = ""
    download_status: str = ""
    download_error: str = ""
    subtask_id: str = Field(
        default="",
        description="Owning subtask (metadata.core_task_slug column)",
    )
    gap_reason: str = Field(
        default="",
        description='"download_failed" or "file_missing"',
    )
