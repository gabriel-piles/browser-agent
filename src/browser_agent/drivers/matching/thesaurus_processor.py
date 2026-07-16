"""Resolve one thesaurus: exact matches, then LLM, then write YAML, then report.

Hides the four-step pipeline per thesaurus
(exact-match partition, LLM call, YAML write, summary +
missing report) behind one object. The match driver calls
:meth:`process` once per thesaurus in the mapping.
"""

from __future__ import annotations

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry
from browser_agent.drivers.matching.thesaurus_exact_matcher import ThesaurusExactMatcher
from browser_agent.drivers.matching.thesaurus_groups_builder import ThesaurusGroupsBuilder
from browser_agent.drivers.matching.thesaurus_llm_caller import ThesaurusLlmCaller
from browser_agent.drivers.matching.thesaurus_match_reporter import ThesaurusMatchReporter
from browser_agent.drivers.matching.thesaurus_yaml_writer import ThesaurusYamlWriter
from browser_agent.ports.llm_port import LlmPort


class ThesaurusProcessor:
    """Resolve one thesaurus end-to-end and write its YAML."""

    def __init__(
        self,
        exact_matcher: ThesaurusExactMatcher,
        groups_builder: ThesaurusGroupsBuilder,
        llm_caller: ThesaurusLlmCaller,
        yaml_writer: ThesaurusYamlWriter,
        reporter: ThesaurusMatchReporter,
    ) -> None:
        self._exact_matcher = exact_matcher
        self._groups_builder = groups_builder
        self._llm_caller = llm_caller
        self._yaml_writer = yaml_writer
        self._reporter = reporter

    async def process(
        self,
        *,
        thesaurus_name: str,
        groups: list[dict],
        llm: LlmPort,
        mapping_default_language: str,
        out_path,
    ) -> None:
        """Resolve one thesaurus: exact, LLM, write, summarise, report missing."""
        thesaurus, thesaurus_values, exact_entries, remaining_map = self._resolve_groups(groups)
        self._reporter.print_header(
            thesaurus_name,
            total=sum(self._groups_builder.combined_counter(groups).values()),
            exact=len(exact_entries),
        )
        llm_entries = await self._llm_caller.call(llm, thesaurus, thesaurus_values, remaining_map)
        mapping_obj = self._yaml_writer.write(
            thesaurus_name,
            thesaurus,
            mapping_default_language,
            llm.model_name,
            exact_entries,
            llm_entries,
            out_path,
        )
        self._reporter.print_summary(out_path, mapping_obj, exact_entries, llm_entries)
        self._reporter.print_missing_reports(groups)

    def _resolve_groups(
        self, groups: list[dict]
    ) -> tuple[ThesauriSnapshot, tuple, list[ThesaurusMappingEntry], dict[str, int]]:
        """Return the thesaurus, its values, the exact entries, and the LLM-bound map."""
        thesaurus: ThesauriSnapshot = groups[0]["thesaurus"]
        thesaurus_values = thesaurus.values
        counter = self._groups_builder.combined_counter(groups)
        exact_entries, remaining_map = self._exact_matcher.partition(counter, thesaurus_values)
        return thesaurus, thesaurus_values, exact_entries, remaining_map
