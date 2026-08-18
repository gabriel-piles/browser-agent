"""Stdlib-only text helpers for emitted scripts.

Site-agnostic: no UN/domain knowledge, no case mutation of stored values.
"""

from __future__ import annotations

import re


def normalize_text(value: str) -> str:
    """Strip, replace non-breaking spaces, collapse internal whitespace.

    Site-agnostic: no case change, no domain knowledge. Returns "" for
    empty/None input.
    """
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def filter_rows(rows, field, keep=None, drop=None):
    """Split rows into (kept, dropped) by explicit regex patterns.

    - No patterns -> keep every row (never silently drop data).
    - keep: list of fullmatch regexes; a row is kept only if its
      normalize_text(row[field]) matches at least one (case-insensitive).
    - drop: list of fullmatch regexes; a matching row is dropped.
    - Every dropped row is printed so data loss is visible, never silent.
    """
    kept, dropped = [], []
    for row in rows:
        v = normalize_text(row.get(field, ""))
        if keep is not None and not any(re.fullmatch(p, v, re.IGNORECASE) for p in keep):
            dropped.append(row)
            print(f"DROP {field}={v!r}: no keep pattern matched")
            continue
        if drop is not None and any(re.fullmatch(p, v, re.IGNORECASE) for p in drop):
            dropped.append(row)
            print(f"DROP {field}={v!r}: drop pattern matched")
            continue
        kept.append(row)
    return kept, dropped
