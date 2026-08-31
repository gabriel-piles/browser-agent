"""The SubtaskSpec the flow explorer emits for one split folder."""

from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.field_spec import FieldSpec


class FlowSubtaskSpec(BaseModel):
    """Exploration result for one split: how its script must be built.

    Mirrors the legacy :class:`SubtaskSpec` but always ``processing``
    (script-type discovery is removed in the flow) and self-contained so
    the writer agent can run with it alone.
    """

    subtask_id: str = Field(
        description="Slug: lowercase alnum + '_', unique within the split flow.",
    )
    description: str = Field(
        description="Self-contained NL instructions for this split: target URL, what to collect, mechanics.",
    )
    verified_selectors: list[str] = Field(
        default_factory=list,
        description="CSS selectors the explorer verified live.",
    )
    field_specs: list[FieldSpec] = Field(
        default_factory=list,
        description="Metadata-field specs the writer pastes verbatim.",
    )
    row_selector: str = Field(
        default="",
        description="CSS selector for the repeated row when one page load yields MANY records; empty = one record per page.",
    )
    sample_document_urls: list[str] = Field(
        default_factory=list,
        description="3-5 DIRECT document file URLs probed live (never listing pages).",
    )
    pdf_download_strategy: str = Field(
        default="browser_fetch",
        description='"curl_cffi" or "browser_fetch" — decided by probing one download live.',
    )
    expected_document_count: int = Field(
        default=0,
        description="Advertised document count observed on the site; 0 when unknown.",
    )
