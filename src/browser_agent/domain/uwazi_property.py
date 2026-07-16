"""One property of a Uwazi template, as seen by the mapping layer.

Wraps the :mod:`uwazi_api` ``PropertySchema`` and exposes only the
fields the apply pipeline actually consumes. Common properties
(``title``, ``creationDate``, etc.) are filtered out upstream.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.field_type import FieldType


class UwaziProperty(BaseModel):
    """A single Uwazi template property."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Property name as declared on the template.")
    label: str = Field(default="", description="UI label; falls back to ``name`` if blank.")
    type: FieldType = Field(description="Normalised property type.")
    required: bool = Field(default=False, description="Whether the template requires this property.")
    thesaurus_id: str | None = Field(
        default=None,
        description="Thesaurus id when the property is select/multiselect, else None.",
    )
    generated_id: bool = Field(
        default=False, description="True for generatedid properties (used for the entity's public id)."
    )
