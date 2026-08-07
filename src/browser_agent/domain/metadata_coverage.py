"""Per-field coverage stats for the ``metadata.db`` catalog.

Snapshot written into the proposed mapping YAML by ``step_3_propose_mapping``
so the operator can see in one place how many entities carry each extracted
field. Optional: older or hand-written mappings omit it and still validate.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MetadataCoverage(BaseModel):
    """Total entity count plus per-field entity counts from ``metadata.db``."""

    model_config = ConfigDict(extra="forbid")

    total_entities: int = Field(
        description="Number of entities (rows) with data in metadata.db.",
    )
    fields: dict[str, int] = Field(
        default_factory=dict,
        description="Field name -> number of entities carrying a value for that field, sorted by count.",
    )
