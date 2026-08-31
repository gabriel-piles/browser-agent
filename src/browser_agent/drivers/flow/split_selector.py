"""Resolve the ``active_flow`` selection into ordered split directories."""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from browser_agent.drivers.flow.active_flow_parser import ActiveFlowSelection

_FLOW_DIR_PATTERN = re.compile(r"^(\d+)_([a-z0-9_]+)$")


def resolve_split_dirs(run_path: Path, selection: ActiveFlowSelection) -> list[Path]:
    """Return the split folders named by the selection, in ascending order.

    A selected order with NO matching folder is logged loudly: the split
    was never created by step 0 (or was renamed), and silently skipping
    it would look like success.
    """
    flow_dir = run_path / "flow"
    if not flow_dir.is_dir():
        logger.error("no flow/ directory under {run} — run step 0 first", run=run_path)
        return []
    by_order: dict[int, Path] = {}
    for entry in sorted(flow_dir.iterdir()):
        match = _FLOW_DIR_PATTERN.match(entry.name)
        if match and entry.is_dir():
            by_order[int(match.group(1))] = entry
    missing = sorted(selection.orders - set(by_order))
    if missing:
        logger.warning(
            "active_flow selected orders with NO split folder: {missing} — they will be skipped",
            missing=missing,
        )
    dirs = [by_order[order] for order in sorted(selection.orders) if order in by_order]
    logger.info("active_flow resolved {n} split folders: {names}", n=len(dirs), names=[d.name for d in dirs])
    return dirs
