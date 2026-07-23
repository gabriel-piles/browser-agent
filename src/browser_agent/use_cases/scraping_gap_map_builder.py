"""Build a compact text summary of what is already in ``metadata.db``.

Gives the validation agent a map of covered categories/years/states so
it can target gaps instead of re-walking paths the scraper already
covered. Reuses :func:`query_rows` and :func:`parse_row_data` from
:mod:`metadata_db`.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from browser_agent.use_cases.metadata_db import parse_row_data, query_rows

_GAP_FIELDS = ("subcategory", "year", "state")
_MAX_SOURCE_ANCHORS = 20


class ScrapingGapMapBuilder:
    """Summarise ``metadata.db`` coverage into a text gap map."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def build(self) -> str:
        """Return a text summary of DB coverage for the agent."""
        rows = query_rows(self._db_path)
        pdf_urls, field_counts, sources = self._summarise(rows)
        if not pdf_urls:
            return self._empty_message()
        return self._render(pdf_urls, field_counts, sources)

    def _summarise(self, rows: list[tuple[str, str, str]]) -> tuple[set[str], dict[str, Counter[str]], list[str]]:
        """Walk rows, collecting pdf_urls, per-field counts, and source anchors."""
        pdf_urls: set[str] = set()
        field_counts: dict[str, Counter[str]] = {f: Counter() for f in _GAP_FIELDS}
        sources: list[str] = []
        for source_url, _slug, data_json in rows:
            data = parse_row_data(data_json)
            url = data.get("pdf_url")
            if url:
                pdf_urls.add(url)
            for field in _GAP_FIELDS:
                value = data.get(field)
                if value:
                    field_counts[field][value] += 1
            if len(sources) < _MAX_SOURCE_ANCHORS:
                sources.append(source_url)
        return pdf_urls, field_counts, sources

    def _render(self, pdf_urls: set[str], field_counts: dict[str, Counter[str]], sources: list[str]) -> str:
        """Render the gap map text from collected stats."""
        lines = [f"Total PDFs in DB: {len(pdf_urls)}"]
        for field in _GAP_FIELDS:
            counts = field_counts[field]
            if counts:
                lines.append(self._render_field(field, counts))
        lines.append(
            "The agent should find PDFs NOT already covered by these categories/years/paths.",
        )
        if not any(field_counts.values()):
            lines.append(self._render_anchors(sources))
        return "\n".join(lines)

    def _render_field(self, field: str, counts: Counter[str]) -> str:
        """Render one field's distribution as ``value: count`` lines."""
        items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        body = "\n".join(f"  {value}: {count}" for value, count in items)
        return f"{field} distribution:\n{body}"

    def _render_anchors(self, sources: list[str]) -> str:
        """Render the source URL anchors fallback."""
        body = "\n".join(f"  {url}" for url in sources)
        return f"Source URLs (first {len(sources)}):\n{body}"

    def _empty_message(self) -> str:
        return "No PDFs found in database. The scraper may have failed entirely."
