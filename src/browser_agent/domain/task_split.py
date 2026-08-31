"""One WHAT-scoped chunk of the task: the documents one split folder owns."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskSplit(BaseModel):
    """One chunk of the task's document set; prompt states WHAT, never HOW."""

    folder_name: str = Field(
        pattern=r"^[a-z0-9_]{1,48}$",
        description="Short descriptive slug, lowercase alnum + '_', unique within the run; the writer prefixes the order number",
    )
    title: str = Field(
        description="Short human label for the split",
    )
    prompt: str = Field(
        description="WHAT scope of this chunk alone: which documents/paths/sessions/languages/document types it owns, introduced by 'THIS CHUNK IS IN CHARGE OF:'. MUST NOT repeat the original task text; never HOW instructions. The original task is passed separately with this prompt",
    )
    page_family: str = Field(
        default="",
        description="Name/description of the single page family this chunk covers; empty only when the agent could not characterize it",
    )
    format_evidence: str = Field(
        default="",
        description="Which sampled pages confirmed the family and its range, and which boundaries stayed unverified",
    )
    covered_paths: list[str] = Field(
        default_factory=list,
        description="Concrete URLs, path prefixes, or document refs this split owns",
    )
    is_new: bool = Field(
        default=False,
        description="True only when created during an incremental re-run pass",
    )
    order: int = Field(
        default=0,
        description="Assigned by the driver when writing the folder; 0 in the agent output",
    )
