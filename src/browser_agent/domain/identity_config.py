"""The key-source a Uwazi mapping uses to identify an entity.

Every :class:`UwaziMapping` has an :class:`IdentityConfig` that
explains where to find the per-record key. ``key_source`` selects
which of the four sources supplies the key:

* ``path_placeholder`` — pull the key from the URL path (the
  default; works when the URL pattern is stable).
* ``field`` — read a specific metadata field from the record
  itself.
* ``key_field_and_property`` — combine a field name with a Uwazi
  property name to look up the entity.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class KeySource(str, Enum):
    """Where the apply pipeline reads the per-record key from."""

    PATH_PLACEHOLDER = "path_placeholder"
    FIELD = "field"
    KEY_FIELD_AND_PROPERTY = "key_field_and_property"


class IdentityConfig(BaseModel):
    """The how and where of the per-record entity key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_source: KeySource = Field(default=KeySource.PATH_PLACEHOLDER)
    key_field: str | None = Field(default=None, description="Source field name when key_source=field.")
    key_property: str | None = Field(
        default=None, description="Uwazi property to match when key_source=key_field_and_property."
    )
    path_placeholder: str | None = Field(default=None, description="Placeholder name in the URL pattern (e.g. 'int').")
    source_url_property: str | None = Field(
        default=None,
        description="Uwazi property that should receive the original source URL.",
    )
