"""Validate + report the ``default_value`` of each select/multiselect field.

Hides the per-field thesaurus lookup, the token-splitting, the
``found``/``missing`` partition, and the per-token print loop
behind one object. The match driver calls :meth:`print_report`
once after loading the mapping and the driver prints a per-
token ``ok`` / ``MISSING`` line for every default value.
"""

from __future__ import annotations

from browser_agent.drivers.console.section_printer import SectionPrinter
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.drivers.matching.thesaurus_lookup import split_default_tokens, thesaurus_for_property


class DefaultValueValidator:
    """Print the default-value vs thesaurus report for select/multiselect fields."""

    def print_report(
        self,
        properties,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        relationships_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> None:
        """Print the default-value report for the properties that have a default_value set."""
        relevant = self._properties_with_default(properties)
        if not relevant:
            return
        SectionPrinter().heading("Default values vs thesauri")
        for prop in relevant:
            self._print_field(prop, template, thesauri_by_id, relationships_by_id)

    def _properties_with_default(self, properties) -> list:
        """Return the select/multiselect/relationship properties that have a non-None ``default_value``."""
        return [
            p
            for p in properties
            if p.type in (FieldType.SELECT, FieldType.MULTI_SELECT, FieldType.RELATIONSHIP) and p.default_value is not None
        ]

    def _print_field(
        self,
        prop,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        relationships_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> None:
        """Print the per-token report for one property, or a status note when empty."""
        template_prop = template.property_by_name(prop.name)
        if template_prop is None:
            print(f"  {prop.name!r}: no such property on the Uwazi template")
            return
        thesaurus, found, missing = self._value_status(prop, template, thesauri_by_id, relationships_by_id)
        if thesaurus is None:
            print(f"  {prop.name!r}: no thesaurus on the Uwazi property")
            return
        if not found and not missing:
            print(f"  {prop.name!r}: empty default_value")
            return
        self._print_tokens(prop.name, thesaurus, found, "ok")
        self._print_tokens(prop.name, thesaurus, missing, "MISSING")

    def _value_status(
        self,
        prop,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        relationships_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> tuple[ThesauriSnapshot | None, list[str], list[str]]:
        """Return ``(thesaurus, found, missing)`` for ``prop.default_value``."""
        thesaurus = self._thesaurus_for_property(prop, template, thesauri_by_id, relationships_by_id)
        tokens = self._tokens(prop)
        if thesaurus is None or not tokens:
            return thesaurus, [], []
        found, missing = self._partition_tokens(tokens, thesaurus)
        return thesaurus, found, missing

    def _thesaurus_for_property(
        self,
        prop,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        relationships_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> ThesauriSnapshot | None:
        """Return the live :class:`ThesauriSnapshot` backing ``prop.name``, or ``None``."""
        if prop.type is FieldType.RELATIONSHIP and relationships_by_id is not None:
            template_prop = template.property_by_name(prop.name)
            if template_prop and template_prop.thesaurus_id:
                return relationships_by_id.get(template_prop.thesaurus_id)
            return None
        return thesaurus_for_property(prop, template, thesauri_by_id)

    def _tokens(self, prop) -> list[str]:
        """Split ``prop.default_value`` into individual tokens for validation."""
        return split_default_tokens(prop)

    def _partition_tokens(
        self,
        tokens: list[str],
        thesaurus: ThesauriSnapshot,
    ) -> tuple[list[str], list[str]]:
        """Split tokens into ``(found, missing)`` against the thesaurus's leaf labels."""
        known = {v.strip().casefold() for v in thesaurus.values}
        found = [t for t in tokens if t.casefold() in known]
        missing = [t for t in tokens if t.casefold() not in known]
        return found, missing

    def _print_tokens(
        self,
        field_target: str,
        thesaurus: ThesauriSnapshot,
        tokens: list[str],
        status: str,
    ) -> None:
        """Print one line per token in ``tokens`` against the thesaurus."""
        for token in tokens:
            print(f"  {field_target!r} -> {token!r}: {status} (thesaurus: {thesaurus.name!r})")
