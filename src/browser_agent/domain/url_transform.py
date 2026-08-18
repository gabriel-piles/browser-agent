"""A URL rewrite applied to listing-derived target hrefs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class UrlTransform(BaseModel):
    """Replace a suffix of a raw href with a new suffix (e.g. swap path)."""

    kind: Literal["replace_suffix"] = "replace_suffix"
    old: str
    new: str
