"""Classify metadata rows as create/update/skip for the upload-validation report.

Hides the row JSON parsing, the per-row ``(action, issues)``
computation, and the action-count aggregation behind one
object. The match driver calls :meth:`classify` once for the
run's metadata.db rows and gets back the action counts the
upload-validation report prints.

The per-issue detectors live in :class:`RowIssueDetector`; this
class only handles the classification + the row parsing.
"""

from __future__ import annotations

import json

from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from browser_agent.drivers.classification.row_issue_detector import RowIssueDetector
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.use_cases.apply_mapping_use_case import (
    resolve_key_value,
    resolve_pdf_filename,
)
from pathlib import Path


class RowIssueClassifier:
    """Classify metadata rows as create/update/skip for the upload-validation report."""

    def __init__(
        self,
        entities_fetcher: ExistingEntitiesFetcher,
        issue_detector: RowIssueDetector,
        downloads_dir: Path | None = None,
    ) -> None:
        self._entities_fetcher = entities_fetcher
        self._issue_detector = issue_detector
        self._downloads_dir = downloads_dir

    def classify(
        self,
        records,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesaurus_lookup: dict,
    ) -> tuple[dict[str, int], list[tuple[str, str, list[str]]]]:
        """Return ``(action counts, [(source_url, title, issues), ...])`` for the rows."""
        entities_by_key = self._index_for(mapping)
        return self._classify_rows(records, mapping, template, thesaurus_lookup, entities_by_key)

    def row_title(self, record: dict, mapping: UwaziMapping) -> str:
        """Return the entity title for one record, falling back to the empty string."""
        title_prop = mapping.title_property()
        if title_prop is not None and title_prop.source:
            value = record.get(title_prop.source)
            if value:
                return str(value)
        return ""

    def classify_one(
        self,
        record: dict,
        source_url: str,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesaurus_lookup: dict,
        entities_by_key: dict[str, str],
    ) -> tuple[str, list[str]]:
        """Return ``(action, issues)`` for one parsed metadata row."""
        key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
        shared_id = self._entities_fetcher.find_existing_shared_id(mapping, key_value, entities_by_key)
        action = "update" if shared_id else "create"
        issues = (
            self._issue_detector.detect(record, source_url, mapping, template, thesaurus_lookup)
            if action == "create"
            else []
        )
        return action, issues

    def _index_for(self, mapping: UwaziMapping) -> dict[str, str]:
        """Fetch and index the existing Uwazi entities once for the whole run."""
        return self._entities_fetcher.fetch(
            template_name=mapping.template,
            language=mapping.default_language,
            key_property=mapping.identity.key_property or "",
        )

    def _classify_rows(
        self,
        records,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesaurus_lookup: dict,
        entities_by_key: dict[str, str],
    ) -> tuple[dict[str, int], list[tuple[str, str, list[str]]]]:
        """Return ``(action counts, [(source_url, title, issues), ...])`` for the rows."""
        counts: dict[str, int] = {"create": 0, "update": 0, "skip": 0}
        issues: list[tuple[str, str, list[str]]] = []
        for source_url, _task_slug, raw_data in records:
            record = self._parse_record(raw_data)
            record.setdefault("pdf_filename", resolve_pdf_filename(record, source_url, self._downloads_dir))
            action, row_issues = self.classify_one(record, source_url, mapping, template, thesaurus_lookup, entities_by_key)
            counts[action] = counts.get(action, 0) + 1
            if action == "create" and row_issues:
                issues.append((source_url, self.row_title(record, mapping), row_issues))
        return counts, issues

    def _parse_record(self, raw_data) -> dict:
        """Decode a metadata row's JSON blob, returning ``{}`` on failure."""
        if not raw_data:
            return {}
        try:
            loaded = json.loads(raw_data)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
