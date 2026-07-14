from __future__ import annotations

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    """A single named run definition from ``runs.yaml``.

    Carries the ``name`` used to create the per-run folder under
    ``data/runs/<name>/`` and the natural-language ``prompt`` that
    drives the script-generation agent.
    """

    name: str = Field(min_length=1, description="Unique run name; becomes the folder name under data/runs/.")
    prompt: str = Field(min_length=1, description="The natural-language task description for this run.")
