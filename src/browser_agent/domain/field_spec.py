"""Declarative metadata-field spec the Explorer emits for the Processing Writer.

The Explorer verifies each metadata field during exploration and records,
per field, the CSS selector that carries the value, the authoritative
read-source, and a sample value. The Processing Writer pastes these specs
verbatim and calls ``extract_fields(tab, FIELD_SPECS)`` instead of
hand-writing a metadata ``tab.evaluate`` IIFE.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FieldSource = Literal["text", "attr", "href", "list_text", "list_attr"]


class FieldSpec(BaseModel):
    """One metadata field the processing script must extract."""

    field: str = Field(description='Metadata key, e.g. "title", "date", "language".')
    selector: str = Field(description="CSS selector for the element(s) carrying the value.")
    source: FieldSource = Field(
        default="text",
        description=(
            "How to read the value: text = first match textContent; "
            "attr = first match named attribute; href = first match href; "
            "list_text/list_attr = all matches as a list (multi-value fields)."
        ),
    )
    attr: str = Field(
        default="",
        description='Attribute name when source is "attr"/"list_attr".',
    )
    sample: str = Field(
        default="",
        description="Value observed during exploration (for validation print).",
    )
    required: bool = Field(
        default=False,
        description="True when the field must be non-empty for a valid record.",
    )
