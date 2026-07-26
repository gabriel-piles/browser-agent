from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from browser_agent.domain.lint_finding import LintFinding


class EmitResult(BaseModel):
    """Outcome of emitting a generated script to disk.

    Carries the on-disk path, the sidecar JSON path, the lint findings
    (if any), the list of transforms that actually matched/applied,
    and the raw LLM python_code persisted before the transform chain.
    """

    script_path: Path
    sidecar_path: Path
    raw_code_path: Path
    lint_findings: list[LintFinding] = Field(default_factory=list)
    transforms_applied: list[str] = Field(default_factory=list)
