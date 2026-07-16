"""Build and write the per-thesaurus :class:`ThesaurusMapping` YAML.

Hides the pydantic-object construction + the YAML serialisation
behind one object. The match driver calls :meth:`write` once
per thesaurus and the writer handles sorting the entries by
``crawl_value`` and dumping with the project's standard YAML
format (no flow style, sort_keys=False, allow_unicode=True).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesaurus_mapping import ThesaurusMapping
from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusYamlWriter:
    """Build the :class:`ThesaurusMapping` pydantic object and dump it as YAML."""

    def write(
        self,
        thesaurus_name: str,
        thesaurus: ThesauriSnapshot,
        mapping_default_language: str,
        llm_model_name: str,
        exact_entries: list[ThesaurusMappingEntry],
        llm_entries: list[ThesaurusMappingEntry],
        out_path: Path,
    ) -> ThesaurusMapping:
        """Build, sort, and dump the :class:`ThesaurusMapping` YAML; return the object."""
        mapping_obj = self._build_payload(
            thesaurus_name,
            thesaurus,
            mapping_default_language,
            llm_model_name,
            exact_entries,
            llm_entries,
        )
        self._dump(out_path, mapping_obj)
        return mapping_obj

    def _build_payload(
        self,
        thesaurus_name: str,
        thesaurus: ThesauriSnapshot,
        mapping_default_language: str,
        llm_model_name: str,
        exact_entries: list[ThesaurusMappingEntry],
        llm_entries: list[ThesaurusMappingEntry],
    ) -> ThesaurusMapping:
        """Build the final :class:`ThesaurusMapping` pydantic object."""
        return ThesaurusMapping(
            thesaurus=thesaurus_name,
            thesaurus_id=thesaurus.thesaurus_id,
            uwazi_name=thesaurus.name,
            default_language=mapping_default_language,
            generated_by=llm_model_name,
            entries=self._sorted_entries(exact_entries, llm_entries),
        )

    def _sorted_entries(
        self,
        exact_entries: list[ThesaurusMappingEntry],
        llm_entries: list[ThesaurusMappingEntry],
    ) -> tuple[ThesaurusMappingEntry, ...]:
        """Return the combined entries sorted by ``crawl_value``."""
        return tuple(sorted(exact_entries + llm_entries, key=lambda e: e.crawl_value))

    def _dump(self, out_path: Path, mapping_obj: ThesaurusMapping) -> None:
        """Write the :class:`ThesaurusMapping` pydantic object as YAML."""
        with out_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                mapping_obj.model_dump(mode="python"),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
