"""Whole-corpus findings the per-row reconciler cannot see.

Orphan files (PDF on disk with no DB row), ``.part`` leftovers from a
crashed mid-download, and identical-size clusters that hint the same
error page was saved repeatedly. These are reported as their own
section because a per-URL check can never surface them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CorpusFinding(BaseModel):
    """A whole-corpus anomaly reported by the reconciler."""

    kind: str = Field(
        description="One of: orphan_file, part_leftover, identical_size_cluster, " + "duplicate_pdf_url, empty_pdf_url.",
    )
    detail: str = Field(description="Human-readable description of the finding.")
    items: list[str] = Field(
        default_factory=list,
        description="Affected filenames or URLs (capped for prompt size).",
    )
