"""Resolve the first non-empty value across candidate source columns.

One pure helper shared by every consumer of ``MappedProperty.source``
so the fallback rule lives in a single place: the title builder, the
metadata value transformer, the row issue detectors, and the thesaurus
groups builder all call :func:`resolve_source_value`.
"""

from __future__ import annotations


def resolve_source_value(record: dict, source: tuple[str, ...] | None) -> object | None:
    """Return the first non-empty record value across candidate source columns, else None."""
    if not source:
        return None
    for name in source:
        value = record.get(name)
        if value is not None and value != "" and not (isinstance(value, (list, tuple)) and not value):
            return value
    return None
