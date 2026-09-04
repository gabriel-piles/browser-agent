"""Input to the download-verification use case."""

from __future__ import annotations

from pydantic import BaseModel, Field


_TASK_DIRECTIVE = (
    "The **Original Task** above is the source of truth. Determine whether "
    "EVERY PDF it requires was downloaded and is intact — not a sample. "
    "Use explore_page to re-walk the site per the prompt, check_pdf to "
    "classify each candidate against the DB + downloads, query_db to "
    "inventory coverage, and run_read_script to cross-reference the DB "
    "against the filesystem. NEVER download a PDF."
)


class VerificationRequest(BaseModel):
    """Input to the verification use case.

    Carries the original task prompt (source of truth), the step 0
    generated script sources (what the scraper actually did), and a
    gap map summarizing what is already in ``metadata.db``.
    """

    task_prompt: str = Field(description="The original run prompt from run.yaml.")
    discovery_script: str = Field(
        default="",
        description="The step 0 discovery script source (empty if no discovery phase).",
    )
    processing_script: str = Field(description="The step 0 processing script source code.")
    gap_map: str = Field(description="Coverage summary from the DB.")
    reconciler_inventory: str = Field(
        default="",
        description="Deterministic DB-vs-disk inventory from the reconciler (ground truth).",
    )
    execution_summary: str = Field(
        default="",
        description="Deterministic tail of the emitted script's execution log (row counts, download results).",
    )
    previous_decision: str = Field(
        default="",
        description="The previous verification round's decision and gap, for delta context.",
    )

    def render_prompt(self) -> str:
        parts = [f"## Original Task\n{self.task_prompt}\n\n---\n\n"]
        if self.discovery_script:
            parts.append(
                f"## Discovery Script (from step 0)\n```python\n{self.discovery_script}\n```\n\n---\n\n",
            )
        parts.append(
            f"## Processing Script (from step 0)\n```python\n{self.processing_script}\n```\n\n---\n\n",
        )
        parts.append(f"## Scraping Coverage (gap map)\n{self.gap_map}\n\n---\n\n")
        if self.reconciler_inventory:
            parts.append(
                f"## Deterministic Reconciler Inventory (DB vs disk)\n{self.reconciler_inventory}\n\n---\n\n",
            )
        if self.execution_summary:
            parts.append(f"## Execution evidence (deterministic, script's own log)\n{self.execution_summary}\n\n---\n\n")
        if self.previous_decision:
            parts.append(f"## Previous verification round (delta context)\n{self.previous_decision}\n\n---\n\n")
        parts.append(_TASK_DIRECTIVE)
        return "".join(parts)
