"""One leaf label of a Uwazi thesaurus.

The :class:`ThesauriSnapshot` flattens the nested
:class:`uwazi_api.domain.thesauri_value.ThesauriValue` tree into a
flat tuple of leaf labels; this is the shape the mapping LLM
matches against. The nested :class:`ThesauriValue` model is kept
around for the rare case where the LLM needs to walk the tree.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ThesauriValue(BaseModel):
    """Recursive representation of one thesaurus node and its children."""

    model_config = ConfigDict(extra="forbid")

    label: str
    id: str
    values: tuple["ThesauriValue", ...] = Field(default_factory=tuple)


ThesauriValue.model_rebuild()
