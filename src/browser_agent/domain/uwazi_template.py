"""A Uwazi template as the mapping layer sees it.

Wraps the :mod:`uwazi_api` ``Template`` object and exposes a
``properties`` tuple of :class:`UwaziProperty`. Common properties
(``creationDate``, ``editDate``) are filtered out so the LLM
and the apply pipeline deal only with domain-specific columns;
the ``title`` common property is exposed separately as
:attr:`title` because the propose step needs to draft a source
for it and the apply step needs to read it back.
"""

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.field_type import FieldType
from browser_agent.domain.uwazi_property import UwaziProperty


class UwaziTemplate(BaseModel):
    """A Uwazi template, normalised for the mapping layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(description="Template name as stored in Uwazi.")
    template_id: str | None = Field(default=None, description="Uwazi internal id, if known.")
    properties: tuple[UwaziProperty, ...] = Field(
        default_factory=tuple,
        description="Domain-specific properties; common properties are filtered out.",
    )
    title: UwaziProperty | None = Field(
        default=None,
        description=(
            "The template's ``title`` common property. Uwazi stores it on the "
            "entity itself (not in ``metadata``); the propose step drafts a "
            "source for it and the apply step reads it back to build "
            "``Entity.title``."
        ),
    )

    default_language: str = Field(default="en", description="ISO language code the template is registered with.")

    def property_by_name(self, name: str) -> UwaziProperty | None:
        """Return the property with the given name, or None when absent."""
        for prop in self.properties:
            if prop.name == name:
                return prop
        return None

    def property_names(self) -> tuple[str, ...]:
        """Return the property names in declaration order."""
        return tuple(p.name for p in self.properties)

    def required_property_names(self) -> tuple[str, ...]:
        """Return the subset of property names marked ``required``."""
        return tuple(p.name for p in self.properties if p.required)

    def select_properties(self) -> tuple[UwaziProperty, ...]:
        """Return every property that points at a Uwazi thesaurus."""
        return tuple(p for p in self.properties if p.type in (FieldType.SELECT, FieldType.MULTI_SELECT))
