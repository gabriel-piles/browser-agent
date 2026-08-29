"""Declarative metadata-field spec the Explorer emits for the Processing Writer.

The Explorer verifies each metadata field during exploration and records,
per field: the CSS selector that carries the value, the authoritative
read-source, whether the value is per-record or page-constant (``scope``),
an optional ordered list of transforms (e.g. strip text inside
parentheses), and a sample value (post-transform).

The Processing Writer pastes these specs verbatim and calls
``extract_fields(tab, FIELD_SPECS)`` for a single-record page (or for
page-scope fields) and ``extract_rows(tab, row_selector, RECORD_FIELD_SPECS)``
for a multi-record page, instead of hand-writing a metadata
``tab.evaluate`` IIFE.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

FieldSource = Literal["text", "attr", "href", "list_text", "list_attr"]
FieldScope = Literal["record", "page"]
FieldTransform = Literal["none", "strip_parentheses", "collapse_whitespace"]


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
    scope: FieldScope = Field(
        default="record",
        description=(
            "record = the value lives in/varies per record container (card/row); "
            "page = the value is CONSTANT for the whole page. Only meaningful when "
            "the subtask's row_selector is set (multi-record page): record-scope "
            "fields feed extract_rows, page-scope fields feed extract_fields once "
            "and are merged into every record."
        ),
    )
    sample: str = Field(
        default="",
        description="Value observed AFTER all transforms (for validation print vs sample).",
    )
    transform: list[FieldTransform] = Field(
        default_factory=list,
        description=(
            "Ordered post-processing steps applied inside extract_fields/extract_rows: "
            "strip_parentheses = remove every balanced (...) group (ASCII and full-width) "
            "then collapse whitespace; collapse_whitespace = one space per whitespace run; "
            "none = no-op. Empty list = trim only."
        ),
    )
    required: bool = Field(
        default=False,
        description="True when the field must be non-empty for a valid record.",
    )

    @field_validator("transform", mode="before")
    @classmethod
    def _coerce_transform(cls, value):
        """Accept a single string for compatibility with older saved plans."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value
