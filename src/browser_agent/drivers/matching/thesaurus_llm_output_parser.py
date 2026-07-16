"""Parse the LLM's YAML output into a list of :class:`ThesaurusMappingEntry`.

Hides the YAML-decoding, per-item coercion, and the
fallback-to-needs-review path for values the LLM did not
cover. The match driver calls :meth:`parse` once per thesaurus
and gets back a list of entries that the YAML writer can
serialise as-is.
"""

from __future__ import annotations

import yaml

from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusLlmOutputParser:
    """Parse one LLM YAML response into a list of :class:`ThesaurusMappingEntry`."""

    def parse(
        self,
        llm_text: str,
        remaining_map: dict[str, int],
        thesaurus_values: tuple[str, ...],
    ) -> list[ThesaurusMappingEntry]:
        """Return entries parsed from ``llm_text``, falling back to needs-review on failure."""
        raw = self._safe_load(llm_text)
        if not isinstance(raw, list):
            return list(self._missing_value_entries(remaining_map, set()))
        return self._parse_entries(raw, remaining_map, thesaurus_values)

    def _safe_load(self, llm_text: str):
        """Parse the LLM YAML output, returning ``None`` on any failure."""
        try:
            return yaml.safe_load(llm_text)
        except Exception:
            return None

    def _parse_entries(
        self,
        raw: list,
        remaining_map: dict[str, int],
        thesaurus_values: tuple[str, ...],
    ) -> list[ThesaurusMappingEntry]:
        """Walk a parsed YAML list and emit one :class:`ThesaurusMappingEntry` per item."""
        seen: set[str] = set()
        entries: list[ThesaurusMappingEntry] = []
        for item in raw:
            entry = self._entry_from_item(item, remaining_map, thesaurus_values)
            if entry is None or entry.crawl_value in seen:
                continue
            seen.add(entry.crawl_value)
            entries.append(entry)
        entries.extend(self._missing_value_entries(remaining_map, seen))
        return entries

    def _entry_from_item(
        self,
        item,
        remaining_map: dict[str, int],
        thesaurus_values: tuple[str, ...],
    ) -> ThesaurusMappingEntry | None:
        """Build one :class:`ThesaurusMappingEntry` from one LLM dict, or ``None``."""
        if not isinstance(item, dict):
            return None
        cv = self._extract_crawl_value(item)
        if cv is None:
            return None
        resolved, needs_review, note = self._resolve_value(item, thesaurus_values)
        return ThesaurusMappingEntry(
            crawl_value=cv,
            uwazi_value=resolved,
            needs_review=needs_review,
            occurrences=remaining_map.get(cv, 0),
            note=note,
        )

    def _extract_crawl_value(self, item) -> str | None:
        """Return the trimmed ``crawl_value`` of an LLM dict, or ``None`` when missing."""
        cv = item.get("crawl_value")
        if not isinstance(cv, str) or not cv.strip():
            return None
        return cv.strip()

    def _resolve_value(
        self,
        item,
        thesaurus_values: tuple[str, ...],
    ) -> tuple[str | None, bool, str | None]:
        """Return the (uwazi_value, needs_review, note) tuple for one LLM dict."""
        uv = self._normalise_uwazi_value(item.get("uwazi_value"))
        if uv is None:
            return None, bool(item.get("needs_review", True)), None
        return self._check_thesaurus_value(uv, thesaurus_values)

    def _normalise_uwazi_value(self, raw) -> str | None:
        """Coerce an LLM-emitted ``uwazi_value`` to a clean string or ``None``."""
        if raw is None or not isinstance(raw, str):
            return None
        return raw.strip() or None

    def _check_thesaurus_value(
        self,
        uv: str,
        thesaurus_values: tuple[str, ...],
    ) -> tuple[str | None, bool, str | None]:
        """Return ``(uwazi_value, needs_review, note)`` after validating against the thesaurus."""
        if uv not in thesaurus_values:
            return None, True, "value not in thesaurus"
        return uv, False, None

    def _missing_value_entries(
        self,
        remaining_map: dict[str, int],
        seen: set[str],
    ) -> tuple[ThesaurusMappingEntry, ...]:
        """Emit needs-review entries for any remaining values the LLM did not cover."""
        return tuple(
            ThesaurusMappingEntry(
                crawl_value=val,
                uwazi_value=None,
                needs_review=True,
                occurrences=count,
                note="llm did not return a mapping for this value",
            )
            for val, count in remaining_map.items()
            if val not in seen
        )
