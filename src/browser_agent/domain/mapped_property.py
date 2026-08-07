"""One property of the Uwazi mapping, merged from template + source.

This model is the single list the operator edits. It contains the
live Uwazi template property metadata (``name``, ``label``, ``type``,
``required``) plus the mapping-specific choices
(``source``, ``default_value``, ``notes``).
The thesaurus reference is excluded from the YAML mapping entirely:
the downstream scripts resolve the thesaurus id from the live Uwazi
template at run time, so the same mapping works against any instance
with the same template (even when the internal thesaurus ids differ).

``template_name`` lists every Uwazi template this property belongs to.
A property shared by the primary and registry templates carries both
names, so the apply pipeline maps it for both at the same time; a
registry-only property carries only the registry name.

Special ``type`` values that are NOT part of ``Entity.metadata``:
- ``title`` (``FieldType.TITLE``) targets the Uwazi entity title. The
  apply step sends the value as ``Entity.title``; the metadata blob
  builder skips it. The ``name`` is always ``"title"`` for these
  entries (it matches the template's ``title`` common property).
- ``file`` (``FieldType.FILE``) targets the entity's primary file.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.llm_field_draft import LlmFieldDraft


class MappedProperty(BaseModel):
    """One target property on Uwazi and how the scraped data fills it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Uwazi property name. Use 'title' for the entity title and 'file' for the primary file.")
    label: str | None = Field(default=None, description="UI label from the Uwazi template.")
    source: tuple[str, ...] | None = Field(
        default=None,
        description="Candidate source column name(s) in the metadata.db row; the first non-empty value wins. None for a constant/default-only entry.",
    )
    default_value: str | None = Field(
        default=None,
        description="Constant value for entries with source=None; None leaves the property unset.",
    )
    template_name: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Templates this property belongs to. A property shared by the primary and "
            "registry templates carries both names; a registry-only property carries only "
            "the registry name. Empty means primary-only (legacy YAML)."
        ),
    )
    type: FieldType = Field(description="Normalised property type.")
    required: bool = Field(default=False, description="Whether the template requires this property.")
    notes: str | None = Field(default=None, description="Free-form human notes for the reviewer.")

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source(cls, value: object) -> object:
        """Coerce a YAML scalar/list into an internal tuple of candidate column names."""
        if value is None:
            return None
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            coerced = tuple(str(v) for v in value)
            return coerced if coerced else None
        return value

    @field_serializer("source")
    def _serialize_source(self, value: tuple[str, ...] | None) -> str | list[str] | None:
        """Collapse a single-element tuple back to a scalar so YAML round-trips as ``source: foo``."""
        if value is None:
            return None
        if len(value) == 1:
            return value[0]
        return list(value)

    @field_validator("default_value", mode="before")
    @classmethod
    def _coerce_default_value(cls, value: object) -> object:
        """Coerce YAML-parsed ``date``/``datetime`` to ISO 8601 strings."""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    @classmethod
    def from_template_and_draft(
        cls,
        template_prop,
        draft: LlmFieldDraft | None,
        template_name: tuple[str, ...] = (),
    ) -> MappedProperty:
        """Merge a live template property with an optional LLM draft.

        When ``draft`` is ``None`` the entry is a source-less default
        placeholder. The ``type`` and ``required`` come from the live
        template so the YAML always reflects the real Uwazi shape.
        ``template_name`` lists every template this property belongs to
        (a shared property carries both the primary and registry names).
        """
        return cls(
            name=template_prop.name,
            label=template_prop.label,
            type=template_prop.type,
            required=template_prop.required,
            source=draft.source if draft is not None else None,
            default_value=draft.default_value if draft is not None else None,
            notes=draft.notes if draft is not None else None,
            template_name=template_name,
        )

    @classmethod
    def title_from_draft(
        cls, title_prop, draft: LlmFieldDraft | None, template_name: tuple[str, ...] = ()
    ) -> MappedProperty:
        """Build the title entry: forced to :attr:`FieldType.TITLE`."""
        entry = cls.from_template_and_draft(title_prop, draft, template_name=template_name)
        return entry.model_copy(update={"type": FieldType.TITLE})

    def match_rank(self) -> int:
        """Sort key: 0 source-backed, 1 default-only, 2 ignored (source=None and default_value=None)."""
        if self.type is FieldType.TITLE:
            return -1
        if self.source is not None:
            return 0
        if self.default_value is not None:
            return 1
        return 2

    @classmethod
    def order_by_match(cls, properties: tuple[MappedProperty, ...]) -> tuple[MappedProperty, ...]:
        """Stable sort: source-backed first, then default-only, then ignored.

        The title entry keeps rank -1 so it stays at the front; the apply
        step reads it as ``Entity.title`` rather than as metadata.
        """
        return tuple(sorted(properties, key=lambda p: p.match_rank()))
