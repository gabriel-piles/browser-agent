from __future__ import annotations

from pydantic import BaseModel, Field


class CodeGenerationRequest(BaseModel):
    """A user's request as it enters the use case.

    Holds the natural-language ``task`` and an optional ``context``
    that callers can use to attach history, site notes, or earlier
    feedback to a re-run. The use case is responsible for converting
    this into a single agent prompt.
    """

    task: str = Field(min_length=1, description="The user's free-form task description.")
    context: str = Field(default="", description="Optional prior context to prepend to the task.")

    def has_context(self) -> bool:
        return bool(self.context and self.context.strip())

    def render_prompt(self) -> str:
        """Render the request into the single prompt the agent sees."""
        if not self.has_context():
            return self.task.strip()
        return f"{self.context.strip()}\n\n---\n\nNew task: {self.task.strip()}"
