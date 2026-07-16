"""Pure mappers from :mod:`uwazi_api` models to the mapping-layer domain models.

These functions take ``uwazi_api`` pydantic objects (``Template``,
``PropertySchema``, ``Thesauri``, ``ThesauriValue``) and return the
frozen domain models the Uwazi-mapping use cases consume
(:class:`UwaziTemplate`, :class:`UwaziProperty`,
:class:`ThesauriSnapshot`, :class:`ThesauriValue`). They hold no
state and never touch the network — the caller fetches the
``uwazi_api`` objects from a :class:`uwazi_api.client.UwaziClient`
and hands them in.
"""

from __future__ import annotations

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesauri_value import ThesauriValue
from browser_agent.domain.uwazi_property import UwaziProperty
from browser_agent.domain.uwazi_template import UwaziTemplate

# ``uwazi_api.PropertyType`` value strings -> the layer's :class:`FieldType`.
_UWAZI_TO_FIELD_TYPE: dict[str, FieldType] = {
    "text": FieldType.TEXT,
    "date": FieldType.DATE,
    "numeric": FieldType.NUMERIC,
    "select": FieldType.SELECT,
    "multiselect": FieldType.MULTI_SELECT,
    "markdown": FieldType.MARKDOWN,
    "link": FieldType.LINK,
    "media": FieldType.FILE,
}


def _normalise_property_type(lib_type) -> FieldType:
    """Map a ``uwazi_api.PropertyType`` member to a :class:`FieldType`."""
    name = str(lib_type.value) if hasattr(lib_type, "value") else str(lib_type)
    if name in _UWAZI_TO_FIELD_TYPE:
        return _UWAZI_TO_FIELD_TYPE[name]
    if name.startswith("multi"):
        return FieldType.MULTI_SELECT
    return FieldType.TEXT


def to_property(schema) -> UwaziProperty:
    """Convert a ``uwazi_api.PropertySchema`` to a :class:`UwaziProperty`."""
    return UwaziProperty(
        name=schema.name or "",
        label=schema.label or "",
        type=_normalise_property_type(schema.type),
        required=schema.required,
        thesaurus_id=schema.content,
        generated_id=schema.generatedId,
    )


def to_template(library_template) -> UwaziTemplate:
    """Convert a ``uwazi_api.Template`` to a :class:`UwaziTemplate`."""
    properties = tuple(to_property(prop) for prop in library_template.properties if not prop.isCommonProperty)
    return UwaziTemplate(
        name=library_template.name,
        template_id=library_template.id,
        properties=properties,
        default_language="en",
    )


def _flatten_thesauri_values(values) -> tuple[str, ...]:
    """Collect leaf labels from a nested ``ThesauriValue`` tree."""
    out: list[str] = []
    stack = list(values or ())
    while stack:
        v = stack.pop()
        if v.values:
            stack.extend(v.values)
        out.append(v.label)
    return tuple(out)


def _to_thesauri_tree(values) -> tuple[ThesauriValue, ...]:
    """Convert a list of ``uwazi_api`` ThesauriValue to a tuple of :class:`ThesauriValue`."""
    return tuple(
        ThesauriValue(
            label=v.label,
            id=v.id,
            values=tuple(ThesauriValue(label=vv.label, id=vv.id, values=tuple()) for vv in (v.values or ())),
        )
        for v in (values or ())
    )


def to_thesauri_snapshot(thesaurus) -> ThesauriSnapshot:
    """Convert a ``uwazi_api.Thesauri`` to a :class:`ThesauriSnapshot`."""
    return ThesauriSnapshot(
        thesaurus_id=thesaurus.id,
        name=thesaurus.name,
        values=_flatten_thesauri_values(thesaurus.values or ()),
        tree=_to_thesauri_tree(thesaurus.values or ()),
    )
