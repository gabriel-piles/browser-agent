"""Input to the download-verification use case."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationRequest(BaseModel):
    """Input to the verification use case.

    Carries the original task prompt (source of truth), the step 0
    generated script source (what the scraper actually did), and a gap
    map summarizing what is already in ``metadata.db``.
    """

    task_prompt: str = Field(description="The original run prompt from run.yaml.")
    generated_script: str = Field(description="The step 0 script source code.")
    gap_map: str = Field(description="Coverage summary from the DB.")

    def render_prompt(self) -> str:
        """Render the request into the single prompt the agent sees."""
        return (
            f"## Original Task\n{self.task_prompt}\n\n"
            f"---\n\n"
            f"## Generated Script (from step 0)\n```python\n{self.generated_script}\n```\n\n"
            f"---\n\n"
            f"## Scraping Coverage (gap map)\n{self.gap_map}\n\n"
            f"---\n\n"
            f"The **Original Task** above is the source of truth. Determine whether "
            f"EVERY PDF it requires was downloaded and is intact — not a sample. "
            f"Use explore_page to re-walk the site per the prompt, check_pdf to "
            f"classify each candidate against the DB + downloads, query_db to "
            f"inventory coverage, and run_read_script to cross-reference the DB "
            f"against the filesystem. NEVER download a PDF."
        )
