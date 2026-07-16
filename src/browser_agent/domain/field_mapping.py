"""One field mapping: a source field + how it lands on a Uwazi property.

The :class:`UwaziMapping` is a tuple of :class:`FieldMapping`. Each
one describes a single column of the source data: its name, the
target Uwazi property, the type, optional thesaurus link, and any
type-specific hints (``parse_formats`` for date strings, etc.).

A :class:`FieldMapping` whose ``source`` is ``None`` carries a
constant ``default_value`` the apply pipeline writes verbatim (after
type-specific coercion) for every record. This lets the mapping
pre-fill template properties that have no matching scraped field;
``default_value=None`` leaves the property unset.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from browser_agent.domain.field_type import FieldType


class FieldMapping(BaseModel):
    """One column in a :class:`UwaziMapping`."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(
        default=None,
        description="Source column name in the metadata.db row; None for a constant-default entry.",
    )
    target: str = Field(description="Target Uwazi property name.")
    type: FieldType = Field(description="Mapping type; selects how the apply pipeline transforms the value.")
    thesaurus: str | None = Field(
        default=None,
        description="Thesaurus name (must match a thesauri_mappings/*.yaml file).",
    )
    parse_formats: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Date parse formats to try in order (date fields).",
    )
    required: bool = Field(default=False, description="Whether the apply pipeline must produce a value for this field.")
    notes: str | None = Field(default=None, description="Free-form human notes for the reviewer.")
    default_value: str | None = Field(
        default=None,
        description="Constant value for entries with source=None; None leaves the property unset. May be a thesaurus leaf label, ISO date, number string, or plain text.",
    )

    @field_validator("default_value", mode="before")
    @classmethod
    def _coerce_default_value(cls, value: object) -> object:
        """Coerce YAML-parsed ``date``/``datetime`` to ISO 8601 strings.

        PyYAML parses an unquoted ``YYYY-MM-DD`` literal as ``datetime.date``;
        we keep ``default_value`` typed as ``str`` so the contract stays simple,
        but accept the YAML-native form rather than failing validation.
        """
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value
