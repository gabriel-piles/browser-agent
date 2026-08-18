"""Discovery targets derived from a listing page (HRC-style session list)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from browser_agent.domain.url_transform import UrlTransform


class ListingTargets(BaseModel):
    """Targets derived by walking links on a listing page.

    For each matching link, optionally parse an integer index from the
    href via ``index_from_href`` (one regex group), filter to
    ``index_range`` (inclusive), apply ``target_url_transform`` to the
    href, and build the label via ``label_template``.
    """

    kind: Literal["derived_from_listing"] = "derived_from_listing"
    listing_url: str
    link_selector: str
    index_from_href: str | None = None
    index_range: tuple[int, int] | None = None
    target_url_transform: UrlTransform | None = None
    label_template: str
