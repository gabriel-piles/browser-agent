"""The full catalog of fields the LLM must map.

Built by ``uwazi_propose`` from the live ``metadata.db`` rows; the
:class:`ProposeMappingUseCase` sends a formatted version of this
catalog to the LLM along with the Uwazi template snapshot.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.metadata_field import MetadataField


class MetadataFieldCatalog(BaseModel):
    """The full set of source fields the LLM must place."""

    model_config = ConfigDict(extra="forbid")

    run: str = Field(description="Run name (the active run from runs.yaml).")
    pattern: str = Field(description="URL pattern of the metadata rows this catalog was built from.")
    sample_urls: tuple[str, ...] = Field(default_factory=tuple)
    fields: tuple[MetadataField, ...] = Field(default_factory=tuple)
    cohesion_assessment: str = Field(
        default="",
        description="A short human-readable note describing how the catalog was derived.",
    )
