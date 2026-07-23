"""Compute the per-row issues a CREATE entity would have on Uwazi.

Hides the title / key / required / pdf issue checks behind one
object so the row classifier can call them by name. The issues
are the human-readable strings the upload-validation report
prints next to each problem row.
"""

from __future__ import annotations

from browser_agent.drivers.classification.existing_entities_fetcher import ExistingEntitiesFetcher
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.use_cases.metadata_value_transformer import build_metadata_for_row
from browser_agent.use_cases.sync_plan_builder import resolve_key_value


class RowIssueDetector:
    """Compute the per-row issues for one metadata row treated as a CREATE."""

    def __init__(self, entities_fetcher: ExistingEntitiesFetcher) -> None:
        self._entities_fetcher = entities_fetcher

    def detect(
        self,
        record: dict,
        source_url: str,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesaurus_lookup: dict,
    ) -> list[str]:
        """Return the non-empty issues for one metadata row."""
        return [
            issue
            for issue in (
                self._title_issue(record, mapping),
                self._key_issue(record, source_url, mapping),
                self._required_issue(record, source_url, mapping, template, thesaurus_lookup),
                self._pdf_issue(record, mapping),
            )
            if issue is not None
        ]

    def _title_issue(self, record: dict, mapping: UwaziMapping) -> str | None:
        """Return the title issue for ``record``, or ``None`` when the title is non-empty."""
        if self._row_title(record, mapping).strip():
            return None
        return "empty title (no source field maps to the Uwazi title property)"

    def _key_issue(self, record: dict, source_url: str, mapping: UwaziMapping) -> str | None:
        """Return the key issue for ``record``, or ``None`` when the key resolves."""
        key_value = resolve_key_value(record, source_url, mapping.identity, mapping)
        if key_value and str(key_value).strip():
            return None
        return "empty key_value (the entity cannot be matched on Uwazi)"

    def _required_issue(
        self,
        record: dict,
        source_url: str,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesaurus_lookup: dict,
    ) -> str | None:
        """Return the required-properties issue, or ``None`` when every required is filled."""
        metadata = build_metadata_for_row(record, source_url, mapping, thesaurus_lookup)
        missing = [n for n in template.required_property_names() if not self._entities_fetcher.has_value(metadata.get(n))]
        if not missing:
            return None
        return f"missing required properties: {', '.join(missing)}"

    def _pdf_issue(self, record: dict, mapping: UwaziMapping) -> str | None:
        """Return the PDF issue for ``record`` when ``upload_pdf`` is true, else ``None``."""
        if not mapping.upload_pdf:
            return None
        if record.get("pdf_filename"):
            return None
        return "upload_pdf is true but the source row has no pdf_filename"

    def _row_title(self, record: dict, mapping: UwaziMapping) -> str:
        """Return the entity title for one record, falling back to the empty string."""
        title_prop = mapping.title_property()
        if title_prop is not None and title_prop.source:
            value = record.get(title_prop.source)
            if value:
                return str(value)
        return ""
