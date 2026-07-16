"""One field observed in the ``metadata.db`` table.

The :class:`MetadataFieldCatalog` is what :class:`uwazi_propose` ships
to the LLM: a list of distinct field names with a few sample values
and a heuristic type guess. The LLM uses it to decide which Uwazi
property each source field should target.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MetadataField(BaseModel):
    """One source field the LLM must place on a Uwazi property."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Field name as it appears in the source record.")
    description: str = Field(default="", description="Free-form description for the LLM prompt.")
    value_type: str = Field(
        default="string",
        description="Heuristic value type: 'string' | 'date' | 'numeric'.",
    )
    examples: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Up to N representative sample values for the LLM.",
    )
    export_to_uwazi: bool = Field(
        default=True,
        description="When False, the LLM is instructed to skip this field.",
    )
