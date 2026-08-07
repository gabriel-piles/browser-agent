from __future__ import annotations

from pydantic import BaseModel, Field


class TaskSplit(BaseModel):
    """The structured handoff from the Explorer agent to the two writer agents.

    The Explorer explores the target site and decomposes the scraping
    task into two focused natural-language prompts — one for the
    Discovery Writer (link collection) and one for the Processing
    Writer (metadata + PDF download). The prompts are self-contained
    instructions that include the target URL, verified CSS selectors,
    filter/scroll/load-more mechanics, and exactly what each script
    should do. The script rules themselves live in each writer's
    system prompt, not here.
    """

    needs_discovery: bool = Field(
        description=(
            "True when the task requires filter iteration, pagination, "
            "or multi-page link collection. False for single-page "
            "extraction tasks (the Discovery Writer is skipped)."
        ),
    )
    discovery_prompt: str = Field(
        description=(
            "Focused natural-language task for the Discovery Writer: "
            "how to collect links, which filters to iterate, which CSS "
            "selectors to use (verified during exploration), and what "
            "the script should do. Must include the target URL."
        ),
    )
    processing_prompt: str = Field(
        description=(
            "Focused natural-language task for the Processing Writer: "
            "how to extract metadata, download PDFs, which CSS selectors "
            "to use (verified during exploration), and what the script "
            "should do. Must include the target URL."
        ),
    )
    site_overview: str = Field(
        description=("Human-readable summary of the site structure for logging and debugging."),
    )
    sample_document_urls: list[str] = Field(
        default_factory=list,
        description=(
            "3-5 sample document page URLs collected during "
            "exploration, used for pre-seeding the discovered_links "
            "table and for processing validation."
        ),
    )
    pdf_download_strategy: str = Field(
        default="browser_fetch",
        description=(
            'The PDF download strategy probed during exploration: "curl_cffi" (curl_cffi succeeded) or "browser_fetch" '
        ),
    )
