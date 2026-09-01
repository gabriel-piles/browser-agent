"""Map the ``active_flow`` split selection to metadata ``task_slug`` values.

Each split folder under ``<run>/flow/<N_name>/`` carries a
``state.json`` whose ``spec.subtask_id`` is the ``task_slug`` stamped
onto every metadata.db row the split's scripts wrote (via
``BROWSER_AGENT_TASK_SLUG``). Resolving slugs through the split
folders lets the upload step push exactly the rows produced by the
splits named in ``active_flow``.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from browser_agent.drivers.flow.active_flow_parser import ActiveFlowSelection
from browser_agent.drivers.flow.split_selector import resolve_split_dirs


def resolve_flow_task_slugs(run_path: Path, selection: ActiveFlowSelection) -> frozenset[str]:
    """Return the ``subtask_id`` slugs of every split named by the selection."""
    slugs: set[str] = set()
    for split_dir in resolve_split_dirs(run_path, selection):
        slug = _slug_of_split(split_dir)
        if slug is None:
            logger.warning(
                "split {name} has no spec.subtask_id in state.json — its rows cannot be selected",
                name=split_dir.name,
            )
            continue
        slugs.add(slug)
    return frozenset(slugs)


def _slug_of_split(split_dir: Path) -> str | None:
    """Return ``spec.subtask_id`` from the split's ``state.json``, or None."""
    state_path = split_dir / "state.json"
    if not state_path.is_file():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    spec = state.get("spec") or {}
    slug = spec.get("subtask_id")
    return str(slug) if slug else None
