from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.run_config import RunConfig


class RunsConfig(BaseModel):
    """Top-level shape of ``runs.yaml``.

    Holds the ``active_run`` name that selects which run the driver
    executes, plus the full list of ``runs``. The loader resolves
    ``active_run`` against the list to produce a single
    :class:`RunConfig`.
    """

    active_run: str = Field(description="Name of the run to execute; must match a name in runs.")
    runs: list[RunConfig] = Field(min_length=1, description="All available run definitions.")

    def active(self) -> RunConfig:
        """Return the run whose name matches ``active_run``."""
        for run in self.runs:
            if run.name == self.active_run:
                return run
        raise ValueError(f"active_run '{self.active_run}' not found in runs")
