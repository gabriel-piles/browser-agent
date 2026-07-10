from __future__ import annotations

from pydantic import BaseModel, Field


class ScriptExecutionResult(BaseModel):
    """Outcome of running a generated script in a subprocess.

    Captures the exit code, combined stdout/stderr output and a
    success flag so the agent can read the result of a validation
    script it wrote and decide whether its strategy is sound before
    producing the final script.
    """

    exit_code: int = Field(
        description="The process exit code. 0 means success.",
    )
    output: str = Field(
        description=(
            "Combined stdout and stderr captured from the subprocess, "
            "truncated to a reasonable size for the agent context window."
        ),
    )
    success: bool = Field(
        description="True when exit_code == 0.",
    )

    def short_output(self, limit: int = 4000) -> str:
        """Return the output truncated to ``limit`` chars with a tail marker."""
        if len(self.output) <= limit:
            return self.output
        return f"{self.output[:limit]}\n... (truncated, total={len(self.output)} chars)"
