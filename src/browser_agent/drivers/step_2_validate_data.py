"""Match extracted metadata values to Uwazi thesaurus values using LLM.

Run this driver after :mod:`browser_agent.drivers.step_1_uwazi_propose`
(or after hand-editing the mapping YAML) and before
:mod:`browser_agent.drivers.step_3_uwazi_apply`. The driver takes
the YAML mapping written by ``step_1_propose_mapping.py``, the live
``metadata.db`` rows for the active run, and the live Uwazi
thesauri; it writes one :class:`ThesaurusMapping` YAML per
thesaurus to ``data/runs/<active_run>/thesauri_mappings/``,
prints a per-field "missing from thesaurus" report so a human
can add values on the Uwazi side, verifies that any
``default_value`` set on a select/multiselect mapping field is
actually present in the live thesaurus, and prints an upload-
validation report: how many entities of the target template
already exist on Uwazi, how many of the run's rows will be
CREATE/UPDATE/SKIP, and per-row issues (empty title, empty
key, missing required property, missing PDF when ``upload_pdf``
is true).

The YAML files are human-editable: fix any LLM mistakes by
hand before running ``step_3_upload_to_uwazi.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3

from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.drivers.matching.default_value_validator import DefaultValueValidator
from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from browser_agent.drivers.matching.match_context_loader import MatchContextLoader
from browser_agent.drivers.mapping.mapping_loader import MappingLoader
from browser_agent.drivers.classification.row_issue_classifier import RowIssueClassifier
from browser_agent.drivers.classification.row_issue_detector import RowIssueDetector
from browser_agent.drivers.paths.run_paths import RunPaths
from browser_agent.drivers.matching.thesaurus_exact_matcher import ThesaurusExactMatcher
from browser_agent.drivers.matching.thesaurus_groups_builder import ThesaurusGroupsBuilder
from browser_agent.drivers.matching.thesaurus_llm_caller import ThesaurusLlmCaller
from browser_agent.drivers.matching.thesaurus_llm_output_parser import ThesaurusLlmOutputParser
from browser_agent.drivers.matching.thesaurus_match_prompt_builder import (
    ThesaurusMatchPromptBuilder,
)
from browser_agent.drivers.matching.thesaurus_match_reporter import ThesaurusMatchReporter
from browser_agent.drivers.matching.thesaurus_processor import ThesaurusProcessor
from browser_agent.drivers.matching.thesaurus_yaml_writer import ThesaurusYamlWriter
from browser_agent.drivers.classification.upload_validation_reporter import UploadValidationReporter
from browser_agent.drivers.clients.uwazi_client_factory import UwaziClientFactory
from browser_agent.use_cases.apply_mapping_use_case import load_thesauri_mappings


class MatchDriver:
    """End-to-end driver: load mapping + DB, run LLM, write YAMLs, print report."""

    def __init__(self) -> None:
        self._paths = RunPaths()
        self._uwazi = UwaziClientFactory()
        self._loader = MappingLoader()
        self._default_validator = DefaultValueValidator()
        self._upload_reporter = UploadValidationReporter()
        self._groups_builder = ThesaurusGroupsBuilder()
        self._exact_matcher = ThesaurusExactMatcher()
        self._prompt_builder = ThesaurusMatchPromptBuilder()
        self._output_parser = ThesaurusLlmOutputParser()
        self._llm_caller = ThesaurusLlmCaller(self._prompt_builder, self._output_parser)
        self._yaml_writer = ThesaurusYamlWriter()
        self._reporter = ThesaurusMatchReporter()
        self._processor = ThesaurusProcessor(
            self._exact_matcher,
            self._groups_builder,
            self._llm_caller,
            self._yaml_writer,
            self._reporter,
        )

    def run(self) -> None:
        """Module entry: run the async match pipeline."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Load the context, run default-value + upload validation, then process thesauri."""
        context_loader = self._build_context_loader()
        context = context_loader.load(self._paths.default_mapping_path())
        self._print_setup_summary(len(context.thesauri_by_id), len(context.field_counters))
        self._default_validator.print_report(context.mapping.properties, context.template, context.thesauri_by_id)
        self._run_upload_validation(context)
        groups = self._build_groups(context)
        if not groups:
            return
        self._paths.thesauri_mappings_dir()
        await self._process_all_thesauri(groups, context)

    def _build_context_loader(self) -> MatchContextLoader:
        """Return a fresh :class:`MatchContextLoader` wired to the live Uwazi client."""
        return MatchContextLoader(
            client=self._uwazi.build(),
            metadata_db_path=self._paths.metadata_db_path(),
            mapping_loader=self._loader,
        )

    def _run_upload_validation(self, context) -> None:
        """Fetch existing entities, classify every row, and print the report."""
        thesaurus_lookup = load_thesauri_mappings(self._paths.thesauri_mappings_dir())
        records = self._query_metadata_rows()
        entities_fetcher = ExistingEntitiesFetcher(context.client)
        issue_detector = RowIssueDetector(entities_fetcher)
        classifier = RowIssueClassifier(entities_fetcher, issue_detector, self._paths.downloads_dir())
        entities_by_key = entities_fetcher.fetch(
            template_name=context.mapping.template,
            language=context.mapping.default_language,
            key_property=context.mapping.identity.key_property or "",
        )
        counts, issues = classifier.classify(records, context.mapping, context.template, thesaurus_lookup)
        self._upload_reporter.print_report(context.mapping, counts, issues, len(entities_by_key))

    def _build_groups(self, context) -> list[dict]:
        """Build the per-thesaurus groups for the active mapping, or print the empty notice."""
        groups = self._groups_builder.build(
            context.mapping,
            context.template,
            context.thesauri_by_id,
            context.field_counters,
        )
        if not groups:
            print("No thesauri with extracted values to match.")
        return groups

    async def _process_all_thesauri(self, groups: list[dict], context) -> None:
        """Process every thesaurus the groups bucket into."""
        llm = OllamaAdapter()
        for thesaurus_name, thesaurus_groups in self._groups_builder.bucket_by_thesaurus(groups).items():
            await self._processor.process(
                thesaurus_name=thesaurus_name,
                groups=thesaurus_groups,
                llm=llm,
                mapping_default_language=context.mapping.default_language,
                out_path=self._paths.default_thesaurus_path(thesaurus_name),
            )
        print("\nDone. Review the YAML files in thesauri_mappings/ " "before running step_3_upload_to_uwazi.py.")

    def _print_setup_summary(self, thesauri_count: int, field_count: int) -> None:
        """Print the one-line summary of the data the driver just loaded."""
        print(f"Loaded {thesauri_count} thesaurus/thesauri")
        print(f"Aggregated distinct values for {field_count} source field(s)")

    def _query_metadata_rows(self) -> list[tuple[str, str, str]]:
        """Return ``(source_url, task_slug, data_json)`` rows from the run's metadata.db."""
        conn = sqlite3.connect(str(self._paths.metadata_db_path()))
        try:
            return conn.execute("SELECT source_url, task_slug, data FROM metadata").fetchall()
        finally:
            conn.close()


def main() -> None:
    """Module entry: invoke the match driver."""
    MatchDriver().run()


if __name__ == "__main__":
    main()
