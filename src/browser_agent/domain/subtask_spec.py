"""Self-contained instructions for one subtask within a scrape plan."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from browser_agent.domain.field_spec import FieldSpec


class SubtaskSpec(BaseModel):
    """One atomic piece of a :class:`ScrapePlan`: a single script's instructions."""

    subtask_id: str = Field(
        description="Slug: lowercase alnum + '_', unique within the plan",
    )
    kind: Literal["discovery", "processing"] = Field(
        default="processing",
        description='"discovery" for link-collection; "processing" for metadata+PDF',
    )
    description: str = Field(
        description="Self-contained NL instructions: target URL, what to collect, mechanics",
    )
    verified_selectors: list[str] = Field(
        default_factory=list,
        description="CSS selectors verified during planning",
    )
    field_specs: list[FieldSpec] = Field(
        default_factory=list,
        description="Metadata-field specs the writer pastes verbatim",
    )
    sample_document_urls: list[str] = Field(
        default_factory=list,
        description="Sample document page URLs for self-check seeding",
    )
    pdf_download_strategy: str = Field(
        default="browser_fetch",
    )
    expected_document_count: int = Field(
        default=0,
        description="Advertised document count; 0 when unknown",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="subtask_ids that must finish before this one starts",
    )
    filter_labels: list[str] = Field(
        default_factory=list,
        description="Filter labels from discovered_links assigned to this subtask; empty for unscoped/single-page tasks",
    )
    reuse_scripts: list[str] = Field(
        default_factory=list,
        description=(
            "Prior scripts the builder should start from, as "
            '"<run_name>/<script_path>" exactly as shown in the '
            "prior-scripts context; empty = write from scratch"
        ),
    )
