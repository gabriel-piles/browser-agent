"""Read the raw ``active_flow`` selector from ``data/active_run.yaml``.

Shared by step 1 (run prompts) and step 5 (upload to Uwazi) so both
resolve the exact same split selection from the single source of truth.
"""

from __future__ import annotations

import yaml

from browser_agent.configuration import RUNS_FILE
from browser_agent.drivers.flow.active_flow_parser import ActiveFlowError


def load_active_flow_raw() -> str:
    """Return the raw ``active_flow`` value from ``active_run.yaml``."""
    data = yaml.safe_load(RUNS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ActiveFlowError(f"active_run.yaml must be a mapping (got {data!r})")
    return str(data.get("active_flow", ""))
