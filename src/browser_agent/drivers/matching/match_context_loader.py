"""Load the full context the match driver needs in one pass.

Loads the active run's :class:`UwaziMapping`, the live
:class:`UwaziTemplate` from Uwazi, the live
``thesaurus_id -> ThesauriSnapshot`` map, the
``thesaurus_id -> ThesauriSnapshot`` map for relationship
properties (whose values are entity titles of the target
template), and the per-field value counters from
``metadata.db``. Bundling them into a single object keeps the
match driver a thin flow.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from browser_agent.drivers.matching.match_context import MatchContext
from browser_agent.drivers.matching.metadata_value_aggregator import MetadataValueAggregator
from browser_agent.drivers.matching.relationship_values_fetcher import RelationshipValuesFetcher
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.use_cases.uwazi_mappers import (
    to_template,
    to_thesauri_snapshot,
)
from uwazi_api.client import UwaziClient


class MatchContextLoader:
    """Load the mapping, template, thesauri, relationships, and field counters for the match driver."""

    def __init__(
        self,
        client: UwaziClient,
        metadata_db_path: Path,
        mapping_loader,
    ) -> None:
        self._client = client
        self._metadata_db_path = metadata_db_path
        self._mapping_loader = mapping_loader

    def load(self, mapping_path: Path) -> MatchContext:
        """Return the bundled :class:`MatchContext` for the active run."""
        mapping = self._mapping_loader.load_or_die(mapping_path)
        template = self._load_template(mapping)
        thesauri_by_id = self._load_thesauri_by_id(mapping)
        relationships_by_id = self._load_relationships_by_id(mapping, template)
        field_counters = self._aggregate_field_counters()
        return MatchContext(
            mapping=mapping,
            template=template,
            thesauri_by_id=thesauri_by_id,
            relationships_by_id=relationships_by_id,
            field_counters=field_counters,
            client=self._client,
        )

    def _load_template(self, mapping: UwaziMapping) -> UwaziTemplate:
        """Resolve the live :class:`UwaziTemplate` matching the mapping's template name."""
        match = self._client.templates.get_by_name(mapping.template)
        if match is None:
            raise ValueError(f"Uwazi template {mapping.template!r} not found")
        return to_template(match)

    def _load_thesauri_by_id(self, mapping: UwaziMapping) -> dict[str, ThesauriSnapshot]:
        """Return the live ``thesaurus_id -> ThesauriSnapshot`` map for the mapping's language."""
        raw = self._client.thesauris.get(language=mapping.default_language) or []
        return {t.thesaurus_id: t for t in (to_thesauri_snapshot(th) for th in raw)}

    def _load_relationships_by_id(self, mapping: UwaziMapping, template: UwaziTemplate) -> dict[str, ThesauriSnapshot]:
        """Return ``thesaurus_id -> ThesauriSnapshot`` for every relationship property."""
        fetcher = RelationshipValuesFetcher(self._client)
        out: dict[str, ThesauriSnapshot] = {}
        for prop in template.properties:
            if prop.type is not FieldType.RELATIONSHIP or not prop.thesaurus_id:
                continue
            target = self._client.templates.get_by_id(prop.thesaurus_id)
            if target is None:
                continue
            out[prop.thesaurus_id] = fetcher.fetch(prop.thesaurus_id, target.name, mapping.default_language)
        return out

    def _aggregate_field_counters(self) -> dict[str, Counter]:
        """Aggregate the per-field value counters from the metadata.db cache."""
        return MetadataValueAggregator(self._metadata_db_path).aggregate()
