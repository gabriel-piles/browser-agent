"""The identity check a Uwazi mapping uses to detect an existing entity.

Every :class:`UwaziMapping` has an :class:`IdentityConfig` that
explains how to tell whether a record was already uploaded to Uwazi:
read the ``key_field`` value from the scraped record and look for an
existing Uwazi entity whose ``key_property`` carries that value.

``select_filtering_name`` + ``select_filtering_options`` narrow
the existing-entity download to Uwazi rows whose value on the
named select property is in the options list; both null/empty
falls back to the unfiltered download.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IdentityConfig(BaseModel):
    """The how and where of the per-record entity key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_field: str | None = Field(
        default=None,
        description="Source field name in the metadata.db row whose value identifies one record.",
    )
    key_property: str | None = Field(
        default=None,
        description="Uwazi property name matched against the key_field value to detect an existing entity.",
    )
    select_filtering_name: str | None = Field(
        default=None,
        description=(
            "Uwazi select property name used to pre-filter the existing-entity download; "
            "None keeps the unfiltered behaviour."
        ),
    )
    select_filtering_options: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Values of ``select_filtering_name`` to keep when downloading existing entities; "
            "empty keeps the unfiltered behaviour."
        ),
    )
