"""One single-page discovery target (optionally scroll/load-more)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SingleTargets(BaseModel):
    """A single page whose links are collected in one place."""

    kind: Literal["single"] = "single"
    label: str
    url: str
