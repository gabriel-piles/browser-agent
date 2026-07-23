from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.configuration import VALIDATION_PDF_COUNT


class ValidationRequest(BaseModel):
    """Input to the validation use case.

    Mirrors :class:`CodeGenerationRequest`: carries the original task
    prompt, the step 0 generated script source, and a gap map
    summarizing what is already in ``metadata.db``.
    """

    task_prompt: str = Field(description="The original run prompt from config.yaml.")
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
            f"Find at least {VALIDATION_PDF_COUNT} PDFs that may be missing. "
            f"Use different navigation paths than the script above. "
            f"Validate each with check_pdf."
        )
