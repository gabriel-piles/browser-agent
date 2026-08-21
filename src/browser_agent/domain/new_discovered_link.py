"""A document link discovered but not yet processed."""

from __future__ import annotations

from pydantic import BaseModel


class NewDiscoveredLink(BaseModel):
    """One ``discovered_links`` row with ``status='discovered'``.

    Either published since the last run or left unprocessed by an
    interrupted run — both are legitimate refresh-pass work.
    """

    url: str
    filter_label: str = ""
