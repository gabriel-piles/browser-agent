"""Resolve the exact-match + LLM-bound split for one thesaurus's value set.

Hides the case-folded exact-match comparison + the
``exact_entries + remaining_map`` partitioning behind one
object. The match driver feeds the per-thesaurus counter into
:meth:`partition` and gets back the already-resolved
:class:`ThesaurusMappingEntry` rows plus the LLM-bound
``{value: count}`` map.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusExactMatcher:
    """Resolve exact matches and leave the remainder for the LLM."""

    def partition(
        self,
        counter: Counter,
        thesaurus_values: tuple[str, ...],
    ) -> tuple[list[ThesaurusMappingEntry], dict[str, int]]:
        """Return ``(exact_entries, remaining_map)`` for ``counter`` against ``thesaurus_values``."""
        exact_entries: list[ThesaurusMappingEntry] = []
        remaining_map: dict[str, int] = {}
        for val, count in counter.most_common():
            entry = self._build_exact_entry(val, count, thesaurus_values)
            if entry is not None:
                exact_entries.append(entry)
            else:
                remaining_map[val] = count
        return exact_entries, remaining_map

    def _build_exact_entry(
        self,
        val: str,
        count: int,
        thesaurus_values: tuple[str, ...],
    ) -> ThesaurusMappingEntry | None:
        """Return an exact-match entry, or ``None`` when no match exists."""
        match = self._exact_match(val, thesaurus_values)
        if match is None:
            return None
        return ThesaurusMappingEntry(
            crawl_value=val,
            uwazi_value=match,
            needs_review=False,
            occurrences=count,
        )

    def _exact_match(self, crawl_value: str, thesaurus_values: Iterable[str]) -> str | None:
        """Return the thesaurus value that casefold-matches ``crawl_value``, or ``None``."""
        folded = crawl_value.strip().casefold()
        for tv in thesaurus_values:
            if tv.strip().casefold() == folded:
                return tv
        return None
