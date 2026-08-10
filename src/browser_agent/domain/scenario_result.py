from __future__ import annotations

from pydantic import BaseModel, Field


class ScenarioResult(BaseModel):
    """Outcome of running one scenario end-to-end.

    Captures whether the generation pipeline produced a working
    script, the failure diagnostics (if any), and the verified
    output metrics (record count, PDF count).
    """

    scenario_name: str = Field(description="Name of the scenario that was run.")
    success: bool = Field(description="True when all expected-output checks passed.")
    failures: list[str] = Field(
        default_factory=list,
        description="Empty when success=True; else human-readable failure messages.",
    )
    emitted_script_path: str | None = Field(
        default=None,
        description="Path to the generated .py script, or None if generation failed.",
    )
    smoke_output: str = Field(
        default="",
        description="Combined stdout+stderr from the emitted script run.",
    )
    driver_exit_code: int = Field(
        default=0,
        description="step_0 driver exit code: 0=ok, 1=smoke fail, 2=run fail.",
    )
    metadata_db_path: str | None = Field(
        default=None,
        description="Path to metadata.db if it exists, else None.",
    )
    pdf_count: int = Field(default=0, description="PDF files found in downloads/.")
    record_count: int = Field(default=0, description="Rows in the metadata table.")
