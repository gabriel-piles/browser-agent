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
    generated script source (what the scraper actually did), and a gap
    map summarizing what is already in ``metadata.db``.
    """

    task_prompt: str = Field(description="The original run prompt from run.yaml.")
    generated_script: str = Field(description="The step 0 script source code.")
    gap_map: str = Field(description="Coverage summary from the DB.")
    step0_explanation: str = Field(
        default="",
        description="The step 0 agent's explanation of selectors, scroll strategy, and mutation order, from the sidecar JSON.",
    )

    def render_prompt(self) -> str:
        parts = [
            f"## Original Task\n{self.task_prompt}\n\n---\n\n",
            f"## Generated Script (from step 0)\n```python\n{self.generated_script}\n```\n\n---\n\n",
        ]
        if self.step0_explanation:
            parts.append(f"## Step 0 Agent Explanation\n{self.step0_explanation}\n\n---\n\n")
        parts.append(f"## Scraping Coverage (gap map)\n{self.gap_map}\n\n---\n\n")
        parts.append(_TASK_DIRECTIVE)
        return "".join(parts)
