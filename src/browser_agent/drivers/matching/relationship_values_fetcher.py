"""Fetch entity titles of a relationship's target template as a snapshot.

A Uwazi ``relationship`` property points at another template (via the
``content`` field on ``PropertySchema``, stored as ``thesaurus_id`` on
:class:`UwaziProperty`). The set of valid values for the relationship
is the set of entity titles that already exist on that target
template. This fetcher paginates through every entity of the target
template and builds a :class:`ThesauriSnapshot` whose ``values`` tuple
holds those titles — the same shape the thesaurus-matching pipeline
consumes, so the exact-matcher, LLM caller, YAML writer, and reporter
all work unchanged for relationships.
"""

from __future__ import annotations

from uwazi_api.client import UwaziClient
from uwazi_api.domain.search_filters import SearchFilters

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot

_PAGE_BATCH = 200


class RelationshipValuesFetcher:
    """Fetch every entity title of a target template as a :class:`ThesauriSnapshot`."""

    def __init__(self, client: UwaziClient) -> None:
        self._client = client

    def fetch(self, target_template_id: str, target_template_name: str, language: str) -> ThesauriSnapshot:
        """Return a snapshot whose ``values`` are every entity title on the target template."""
        titles = self._fetch_all_titles(target_template_name, language)
        return ThesauriSnapshot(
            thesaurus_id=target_template_id,
            name=target_template_name,
            values=tuple(titles),
            all_labels=tuple(titles),
            tree=(),
        )

    def _fetch_all_titles(self, template_name: str, language: str) -> list[str]:
        """Return every entity title for ``template_name`` via paginated search."""
        out: list[str] = []
        start = 0
        while True:
            page = self._fetch_page(template_name, start, language)
            if not page:
                break
            out.extend(e.title for e in page if e.title)
            if len(page) < _PAGE_BATCH:
                break
            start += _PAGE_BATCH
        return out

    def _fetch_page(self, template_name: str, start: int, language: str) -> list:
        """Fetch one page of entities for ``template_name`` starting at ``start``."""
        return self._client.search.search_by_filter(
            filters=SearchFilters(filters={}),
            template_name=template_name,
            start_from=start,
            batch_size=_PAGE_BATCH,
            language=language,
        )
