"""One property of the Uwazi mapping, merged from template + source.

This model is the single list the operator edits. It contains the
live Uwazi template property metadata (``name``, ``label``, ``type``,
``required``) plus the mapping-specific choices
(``source``, ``thesaurus``, ``default_value``, ``notes``).
The thesaurus id is excluded from the YAML mapping — operators refer
to the thesaurus by its name and the downstream scripts resolve the
id from the live Uwazi template.

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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.llm_field_draft import LlmFieldDraft


class MappedProperty(BaseModel):
    """One target property on Uwazi and how the scraped data fills it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Uwazi property name. Use 'title' for the entity title and 'file' for the primary file.")
    label: str | None = Field(default=None, description="UI label from the Uwazi template.")
    type: FieldType = Field(description="Normalised property type.")
    required: bool = Field(default=False, description="Whether the template requires this property.")
    source: str | None = Field(
        default=None,
        description="Source column name in the metadata.db row; None for a constant/default-only entry.",
    )
    thesaurus: str | None = Field(
        default=None,
        description="Thesaurus name (must match a thesauri_mappings/*.yaml file).",
    )
    default_value: str | None = Field(
        default=None,
        description="Constant value for entries with source=None; None leaves the property unset.",
    )
    notes: str | None = Field(default=None, description="Free-form human notes for the reviewer.")
    template_name: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Templates this property belongs to. A property shared by the primary and "
            "registry templates carries both names; a registry-only property carries only "
            "the registry name. Empty means primary-only (legacy YAML)."
        ),
    )

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
            thesaurus=draft.thesaurus if draft is not None else None,
            default_value=draft.default_value if draft is not None else None,
            notes=draft.notes if draft is not None else None,
            template_name=template_name,
        )

    @classmethod
    def title_from_draft(
        cls, title_prop, draft: LlmFieldDraft | None, template_name: tuple[str, ...] = ()
    ) -> MappedProperty:
        """Build the title entry: forced to :attr:`FieldType.TITLE`, thesaurus dropped."""
        entry = cls.from_template_and_draft(title_prop, draft, template_name=template_name)
        return entry.model_copy(update={"type": FieldType.TITLE, "thesaurus": None})

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
