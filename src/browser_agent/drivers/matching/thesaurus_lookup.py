"""Shared thesaurus lookup helpers for the match driver.

Two match-driver classes (:class:`ThesaurusGroupsBuilder` and
:class:`DefaultValueValidator`) duplicate the same two operations:
splitting a ``default_value`` into tokens and resolving the live
:class:`ThesauriSnapshot` for a mapping property. Centralising them
keeps the two in sync.
"""

from __future__ import annotations

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_template import UwaziTemplate


def split_default_tokens(prop) -> list[str]:
    """Split ``prop.default_value`` into individual comma-separated tokens."""
    if not prop.default_value:
        return []
    return [token for token in (t.strip() for t in str(prop.default_value).split(",")) if token]


def thesaurus_for_property(
    prop, template: UwaziTemplate, thesauri_by_id: dict[str, ThesauriSnapshot]
) -> ThesauriSnapshot | None:
    """Return the live :class:`ThesauriSnapshot` backing ``prop.name``, or ``None``."""
    template_prop = template.property_by_name(prop.name)
    if template_prop is None or template_prop.thesaurus_id is None:
        return None
    return thesauri_by_id.get(template_prop.thesaurus_id)
