"""The discovery manifest: structured contract for a discovery script."""

from __future__ import annotations

from typing import Annotated, Union

from pydantic import BaseModel, Field

from browser_agent.domain.fixed_targets import FixedTargets
from browser_agent.domain.listing_targets import ListingTargets
from browser_agent.domain.single_targets import SingleTargets

DiscoveryTargets = Annotated[
    Union[FixedTargets, ListingTargets, SingleTargets],
    Field(discriminator="kind"),
]


class DiscoveryManifest(BaseModel):
    """Structured description of a discovery script's collection shape.

    Emitted as a module-level ``DISCOVERY_MANIFEST = {...}`` dict literal
    in the script; parsed via ``ast.literal_eval`` and validated here.
    """

    targets: DiscoveryTargets
    count_selector: str
    count_scope: str | None = None
    min_per_target: int = 1
    max_links_per_item: int = 1
    load_more_selector: str = ""
