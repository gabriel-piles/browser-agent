"""Fetch and index the entities Uwazi already has for one template.

Hides the paginated search-by-filter call, the metadata scalar
coercion, the ``key_property -> shared_id`` index build, and the
"has any value" predicate behind one object. The match driver
calls :meth:`fetch` once and gets back a dict the row
classifier can probe with ``O(1)`` lookups.
"""

from __future__ import annotations

from browser_agent.domain.identity_config import KeySource
from browser_agent.domain.uwazi_mapping import UwaziMapping
from uwazi_api.client import UwaziClient
from uwazi_api.domain.search_filters import SearchFilters

# Page size used when iterating Uwazi's search-by-filter results.
_PAGE_BATCH = 100


class ExistingEntitiesFetcher:
    """Fetch and index the entities Uwazi already has for one template."""

    def __init__(self, client: UwaziClient) -> None:
        self._client = client

    def fetch(
        self,
        template_name: str,
        language: str,
        key_property: str,
    ) -> list:
        """Return every Uwazi entity for ``template_name`` indexed by ``key_property``."""
        entities = self._fetch_all(template_name, language)
        return self._index_by_key(entities, key_property)

    def _fetch_all(self, template_name: str, language: str) -> list:
        """Fetch every entity for ``template_name`` via paginated search."""
        out: list = []
        start = 0
        while True:
            page = self._fetch_page(template_name, language, start, _PAGE_BATCH)
            out.extend(page)
            if len(page) < _PAGE_BATCH:
                break
            start += _PAGE_BATCH
        return out

    def _fetch_page(self, template_name: str, language: str, start: int, batch: int) -> list:
        """Fetch one page of entities for ``template_name`` starting at ``start``."""
        return (
            self._client.search.search_by_filter(
                filters=SearchFilters(filters={}),
                template_name=template_name,
                start_from=start,
                batch_size=batch,
                language=language,
            )
            or []
        )

    def _index_by_key(self, entities: list, key_property: str) -> dict[str, str]:
        """Index entities by the first scalar value of ``key_property`` in their metadata."""
        if not key_property:
            return {}
        out: dict[str, str] = {}
        for ent in entities:
            for prop_name, prop_value in (ent.metadata or {}).items():
                if prop_name != key_property:
                    continue
                value = self._scalar_from_metadata(prop_value)
                if value:
                    out.setdefault(value, ent.shared_id or ent.id or "")
        return out

    def _scalar_from_metadata(self, raw) -> str | None:
        """Coerce a Uwazi metadata value (dict|list|scalar) to a flat string, or ``None``."""
        if raw is None:
            return None
        if isinstance(raw, dict):
            v = raw.get("value")
            return str(v).strip() if v is not None else None
        if isinstance(raw, list):
            for item in raw:
                v = self._scalar_from_metadata(item)
                if v is not None:
                    return v
            return None
        text = str(raw).strip()
        return text or None

    def find_existing_shared_id(
        self,
        mapping: UwaziMapping,
        key_value,
        entities_by_key: dict[str, str],
    ) -> str | None:
        """Return the existing Uwazi shared id for ``key_value``, or ``None``."""
        if mapping.identity.key_source is not KeySource.KEY_FIELD_AND_PROPERTY:
            return None
        if not mapping.identity.key_property or not key_value:
            return None
        return entities_by_key.get(str(key_value).strip())

    def has_value(self, raw) -> bool:
        """Return True when ``raw`` carries a non-empty value (scalar, list, or dict)."""
        if raw is None:
            return False
        if isinstance(raw, (dict, list)):
            return len(raw) > 0
        return str(raw).strip() != ""
