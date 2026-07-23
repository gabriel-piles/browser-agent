"""Apply post-LLM fallbacks to a drafted :class:`UwaziMapping`.

The :class:`ProposeMappingUseCase` delegates here to append
``source=None`` default entries for template properties the LLM
left uncovered and to correct parent-group default values to leaf
labels. The filler receives the live Uwazi template so it can
synthesise the missing entries from real template metadata.
"""

from __future__ import annotations


from browser_agent.domain.field_type import FieldType
from browser_agent.domain.mapped_property import MappedProperty
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesauri_value import ThesauriValue
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_property import UwaziProperty
from browser_agent.domain.uwazi_template import UwaziTemplate


def _find_tree_node(tree: tuple[ThesauriValue, ...], label: str) -> ThesauriValue | None:
    """Return the ThesauriValue node with ``label`` in ``tree`` or None; recurses."""
    for node in tree:
        if node.label == label:
            return node
        if node.values:
            found = _find_tree_node(node.values, label)
            if found is not None:
                return found
    return None


def _first_leaf_label(node: ThesauriValue) -> str | None:
    """Return the first leaf label in the subtree rooted at ``node``, or None when empty."""
    current = node
    while current.values:
        current = current.values[0]
    return current.label


def _resolve_thesaurus_name(thesaurus_id: str | None, thesauri_by_id: dict[str, ThesauriSnapshot]) -> str | None:
    """Return the canonical thesaurus name for ``thesaurus_id``, or None when absent."""
    snapshot = thesauri_by_id.get(thesaurus_id) if thesaurus_id else None
    return snapshot.name if snapshot else None


def _with_thesaurus(prop: MappedProperty, name: str) -> MappedProperty:
    """Return ``prop`` with ``thesaurus`` set to ``name`` when it is currently None."""
    return prop if prop.thesaurus is not None else prop.model_copy(update={"thesaurus": name})


def _correct_one(
    prop: MappedProperty,
    thesauri_by_id: dict[str, ThesauriSnapshot],
    template: UwaziTemplate,
) -> MappedProperty:
    """Return ``prop`` unchanged, or copy-corrected when its default_value is a parent group."""
    template_prop = template.property_by_name(prop.name) if template else None
    thesaurus_id = template_prop.thesaurus_id if template_prop else None
    if not thesaurus_id:
        return prop
    thesaurus = thesauri_by_id.get(thesaurus_id)
    if thesaurus is None:
        return prop
    prop = _with_thesaurus(prop, thesaurus.name)
    if prop.default_value in thesaurus.values:
        return prop
    node = _find_tree_node(thesaurus.tree, prop.default_value)
    if node is None:
        return prop
    first_leaf = _first_leaf_label(node)
    if first_leaf is None or first_leaf == prop.default_value:
        return prop
    base = prop.notes or ""
    suffix = f"default_value corrected from {prop.default_value!r} to leaf label {first_leaf!r}"
    return prop.model_copy(update={"default_value": first_leaf, "notes": f"{base}; {suffix}" if base else suffix})


class MappingFallbackFiller:
    """Patch a drafted :class:`UwaziMapping` with sensible defaults."""

    def apply(
        self,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot] | None = None,
    ) -> None:
        """Append missing default entries and correct parent-group defaults."""
        self._fill_missing_defaults(mapping, template, thesauri_by_id or {})
        mapping.properties = tuple(_correct_one(p, thesauri_by_id or {}, template) for p in mapping.properties)

    def _fill_missing_defaults(
        self,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> None:
        """Append ``source=None`` entries for template properties the LLM missed.

        The title is added by :class:`LlmDraftAssembler` from the
        template's common properties; we never auto-append a default
        for it (title lives on ``Entity.title``, not in metadata).
        """
        covered = self._mapped_targets(mapping)
        if template.title is not None:
            covered.add(template.title.name)
        missing = [p for p in template.properties if p.name not in covered]
        if not missing:
            return
        mapping.properties = mapping.properties + tuple(self._default_entry(p, thesauri_by_id) for p in missing)

    def _mapped_targets(self, mapping: UwaziMapping) -> set[str]:
        """Return the Uwazi property names already covered by the mapping."""
        return {p.name for p in mapping.properties}

    def _default_entry(
        self,
        prop: UwaziProperty,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> MappedProperty:
        """Build a ``source=None`` :class:`MappedProperty` placeholder for one property."""
        return MappedProperty(
            name=prop.name,
            label=prop.label,
            type=prop.type,
            required=prop.required,
            source=None,
            thesaurus=_resolve_thesaurus_name(prop.thesaurus_id, thesauri_by_id),
            default_value=self._default_value_for(prop, thesauri_by_id),
            notes="unmapped template property; no matching scraped field",
        )

    def _default_value_for(
        self,
        prop: UwaziProperty,
        thesauri_by_id: dict[str, ThesauriSnapshot],
    ) -> str | None:
        """Return a sensible default for ``prop``, or None when not required."""
        if not prop.required:
            return None
        if prop.type in (FieldType.SELECT, FieldType.MULTI_SELECT) and prop.thesaurus_id:
            thesaurus = thesauri_by_id.get(prop.thesaurus_id)
            if thesaurus and thesaurus.values:
                return thesaurus.values[0]
        return None
