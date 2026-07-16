"""Mapping from :class:`FieldMapping.type` strings to internal enum values.

Uwazi templates declare property types like ``select``, ``multiselect``,
``date``, ``text``. The mapping layer normalises them to a fixed
:class:`FieldType` so the rest of the apply pipeline can pattern-match
on enum members without string-comparing.
"""

from __future__ import annotations

from enum import Enum


class FieldType(str, Enum):
    """Normalised property types used by the Uwazi mapping layer."""

    TITLE = "title"
    DATE = "date"
    TEXT = "text"
    NUMERIC = "numeric"
    SELECT = "select"
    MULTI_SELECT = "multiselect"
    MARKDOWN = "markdown"
    LINK = "link"
    FILE = "file"
    SKIPPED = "skipped"
