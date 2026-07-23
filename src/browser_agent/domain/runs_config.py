from __future__ import annotations

from pydantic import BaseModel, Field


class RunsConfig(BaseModel):
    """Top-level shape of ``runs.yaml``.

    Holds only the ``active_run`` name that selects which run the driver
    executes. The per-run configuration (template, prompt, etc.) is
    loaded from ``data/runs/<active_run>/config.yaml``.
    """

    active_run: str = Field(description="Name of the run to execute.")
