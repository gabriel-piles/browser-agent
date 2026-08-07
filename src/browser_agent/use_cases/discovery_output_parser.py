"""Decode the discovery script's UNDER-COLLECTED output markers.

The discovery script prints ``UNDER-COLLECTED`` on a
``--- <filter_label> ---`` header line when a filter value's discovered
count is below the site-advertised total. These functions extract those
filter labels so the repair prompt can name them.
"""

from __future__ import annotations


def path_header(line: str) -> str | None:
    """Return the path from a ``--- <path> ---`` header line, or None."""
    stripped = line.strip()
    if not (stripped.startswith("--- ") and stripped.endswith(" ---")):
        return None
    return stripped[4:-4].strip() or None


def under_collected_paths(output: str) -> list[str]:
    """Extract the paths flagged UNDER-COLLECTED from the discovery script output."""
    paths: list[str] = []
    for line in output.splitlines():
        if "UNDER-COLLECTED" not in line:
            continue
        header = path_header(line)
        if header:
            paths.append(header)
    return paths
