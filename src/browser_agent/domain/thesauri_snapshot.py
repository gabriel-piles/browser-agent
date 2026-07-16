"""Flattened view of a Uwazi thesaurus for the mapping layer.

The :mod:`uwazi_api` ``Thesauri`` model returns a tree of
:class:`ThesauriValue`; the mapping layer only cares about the leaf
labels, so :class:`ThesauriSnapshot` keeps the tree (for advanced
cases) and exposes a flat ``values`` tuple of every leaf label.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.thesauri_value import ThesauriValue


class ThesauriSnapshot(BaseModel):
    """A Uwazi thesaurus, flattened to leaf labels."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thesaurus_id: str = Field(description="Uwazi internal thesaurus id.")
    name: str = Field(description="Thesaurus name as stored in Uwazi.")
    values: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Flat list of every leaf label in the thesaurus.",
    )
    tree: tuple[ThesauriValue, ...] = Field(
        default_factory=tuple,
        description="Recursive tree of ThesauriValue nodes, for advanced use.",
    )
