"""Flattened view of a Uwazi thesaurus for the mapping layer.

The :mod:`uwazi_api` ``Thesauri`` model returns a tree of
:class:`ThesauriValue`; the mapping layer needs three views of it:

* ``values`` — every **leaf** label in DFS order. This is the safe
  set for select/multiselect properties: a single ``select`` field
  can only store a leaf, so the prompt, fallback filler, and
  validators all consult this set. Crucially it does **not** include
  parent group names, which would let a non-leaf slip through.
* ``all_labels`` — every label, parent and leaf, in DFS order. For
  displaying the thesaurus structure or warning an operator that a
  named group still has children.
* ``tree`` — the recursive :class:`ThesauriValue` tree, for any
  consumer that needs to walk parents vs. leaves.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.thesauri_value import ThesauriValue


class ThesauriSnapshot(BaseModel):
    """A Uwazi thesaurus, flattened to leaves, all labels, and a tree."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    thesaurus_id: str = Field(description="Uwazi internal thesaurus id.")
    name: str = Field(description="Thesaurus name as stored in Uwazi.")
    values: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Flat list of every leaf label in the thesaurus. Safe for select/multiselect defaults.",
    )
    all_labels: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Flat list of every label in the thesaurus, parents and leaves, in DFS order.",
    )
    tree: tuple[ThesauriValue, ...] = Field(
        default_factory=tuple,
        description="Recursive tree of ThesauriValue nodes, for advanced use.",
    )
