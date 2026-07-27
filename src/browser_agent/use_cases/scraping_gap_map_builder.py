"""Build a compact text summary of what is already in ``metadata.db``.

Gives the validation agent a map of covered categories/years/states so
it can target gaps instead of re-walking paths the scraper already
covered. Reuses :func:`query_rows` and :func:`parse_row_data` from
:mod:`metadata_db`.

Computes actual *gaps* rather than a census: holes in dense numeric
ranges (year 2019 and 2021 present, 2020 absent) and zero-row cells in
the year × state cross-product. That surfaces "the filter loop skipped
a value" directly instead of asking the model to infer it from a
distribution table. Per-field distinct values are capped so a large DB
does not produce a huge prompt.
"""

from __future__ import annotations

from collections import Counter
from itertools import product
from pathlib import Path

from browser_agent.use_cases.metadata_db import parse_row_data, query_rows

_GAP_FIELDS = ("subcategory", "year", "state")
_MAX_SOURCE_ANCHORS = 20
_MAX_FIELD_VALUES = 15
_NUMERIC_GAP_THRESHOLD = 3


class ScrapingGapMapBuilder:
    """Summarise ``metadata.db`` coverage into a text gap map."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def build(self) -> str:
        """Return a text summary of DB coverage for the agent."""
        rows = query_rows(self._db_path)
        pdf_urls, field_counts, sources, year_state = self._summarise(rows)
        if not pdf_urls:
            return self._empty_message()
        return self._render(pdf_urls, field_counts, sources, year_state)

    def _summarise(
        self, rows: list[tuple[str, str, str]]
    ) -> tuple[set[str], dict[str, Counter[str]], list[str], dict[tuple[str, str], int]]:
        """Walk rows, collecting pdf_urls, per-field counts, source anchors, year×state."""
        pdf_urls: set[str] = set()
        field_counts: dict[str, Counter[str]] = {f: Counter() for f in _GAP_FIELDS}
        sources: list[str] = []
        year_state: dict[tuple[str, str], int] = {}
        for source_url, _slug, data_json in rows:
            data = parse_row_data(data_json)
            url = data.get("pdf_url")
            if url:
                pdf_urls.add(url)
            for field in _GAP_FIELDS:
                value = data.get(field)
                if value:
                    field_counts[field][str(value)] += 1
            year = str(data.get("year", "") or "")
            state = str(data.get("state", "") or "")
            if year and state:
                year_state[(year, state)] = year_state.get((year, state), 0) + 1
            if len(sources) < _MAX_SOURCE_ANCHORS:
                sources.append(source_url)
        return pdf_urls, field_counts, sources, year_state

    def _render(
        self,
        pdf_urls: set[str],
        field_counts: dict[str, Counter[str]],
        sources: list[str],
        year_state: dict[tuple[str, str], int],
    ) -> str:
        """Render the gap map text from collected stats."""
        lines = [f"Total PDFs in DB: {len(pdf_urls)}"]
        for field in _GAP_FIELDS:
            counts = field_counts[field]
            if counts:
                lines.append(self._render_field(field, counts))
        gaps = self._numeric_gaps(field_counts["year"])
        if gaps:
            lines.append(self._render_numeric_gaps("year", gaps))
        cross = self._year_state_gaps(year_state, field_counts)
        if cross:
            lines.append(self._render_year_state_gaps(cross))
        lines.append(
            "The agent should find PDFs NOT already covered by these categories/years/paths.",
        )
        if not any(field_counts.values()):
            lines.append(self._render_anchors(sources))
        return "\n".join(lines)

    def _render_field(self, field: str, counts: Counter[str]) -> str:
        """Render one field's distribution, capped to the top values."""
        items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        shown = items[:_MAX_FIELD_VALUES]
        body = "\n".join(f"  {value}: {count}" for value, count in shown)
        footer = f"\n  ... ({len(items) - len(shown)} more)" if len(items) > len(shown) else ""
        return f"{field} distribution:\n{body}{footer}"

    def _numeric_gaps(self, counts: Counter[str]) -> list[int]:
        """Return missing integers inside the dense year range."""
        years: list[int] = []
        for value in counts:
            try:
                years.append(int(value))
            except ValueError:
                continue
        if len(years) < _NUMERIC_GAP_THRESHOLD:
            return []
        lo, hi = min(years), max(years)
        present = set(years)
        return [y for y in range(lo, hi + 1) if y not in present]

    def _render_numeric_gaps(self, field: str, gaps: list[int]) -> str:
        body = ", ".join(str(y) for y in gaps)
        return f"{field} gaps (missing inside observed range): {body}"

    def _year_state_gaps(
        self,
        year_state: dict[tuple[str, str], int],
        field_counts: dict[str, Counter[str]],
    ) -> list[tuple[str, str]]:
        """Return zero-row (year, state) cells in the cross-product."""
        years = sorted(field_counts["year"].keys())
        states = sorted(field_counts["state"].keys())
        if not years or not states:
            return []
        cells = list(product(years, states))
        return [(y, s) for y, s in cells if (y, s) not in year_state]

    def _render_year_state_gaps(self, gaps: list[tuple[str, str]]) -> str:
        body = "\n".join(f"  {y} × {s}" for y, s in gaps[:_MAX_FIELD_VALUES])
        footer = f"\n  ... ({len(gaps) - _MAX_FIELD_VALUES} more)" if len(gaps) > _MAX_FIELD_VALUES else ""
        return f"year × state gaps (zero-row cells):\n{body}{footer}"

    def _render_anchors(self, sources: list[str]) -> str:
        """Render the source URL anchors fallback."""
        body = "\n".join(f"  {url}" for url in sources)
        return f"Source URLs (first {len(sources)}):\n{body}"

    def _empty_message(self) -> str:
        return "No PDFs found in database. The scraper may have failed entirely."
