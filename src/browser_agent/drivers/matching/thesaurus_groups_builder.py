"""Build the per-thesaurus groups the match driver iterates over.

Hides the per-field select/multiselect filtering, the thesaurus
lookup for each field's target property, the bucketing-by-thesaurus
step, and the cross-field counter merge behind one object. The
match driver calls :meth:`build` once for the mapping and
:meth:`bucket_by_thesaurus` to group the resulting groups.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from browser_agent.drivers.console.section_printer import SectionPrinter
from browser_agent.domain.field_type import FieldType
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate


class ThesaurusGroupsBuilder:
    """Build per-thesaurus groups from a mapping, template, and field counters."""

    def build(
        self,
        mapping: UwaziMapping,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        field_counters: dict[str, Counter],
    ) -> list[dict]:
        """Return one group dict per select/multiselect property with extracted values."""
        groups: list[dict] = []
        skips: list[str] = []
        for prop in mapping.properties:
            if prop.type not in (FieldType.SELECT, FieldType.MULTI_SELECT):
                continue
            counter = field_counters.get(prop.source, Counter())
            group, skip = self._build_one(prop, template, thesauri_by_id, counter)
            if group is not None:
                groups.append(group)
            elif skip:
                skips.append(skip)
        self._print_skips(skips)
        return groups

    def bucket_by_thesaurus(self, groups: list[dict]) -> dict[str, list[dict]]:
        """Bucket the per-property groups by their canonical thesaurus name."""
        out: dict[str, list[dict]] = {}
        for group in groups:
            out.setdefault(self._thesaurus_name_of(group), []).append(group)
        return out

    def combined_counter(self, groups: Iterable[dict]) -> Counter:
        """Merge the value Counters of every group that targets the same thesaurus."""
        counter: Counter = Counter()
        for group in groups:
            counter.update(group["counter"])
        return counter

    def _build_one(
        self,
        prop,
        template: UwaziTemplate,
        thesauri_by_id: dict[str, ThesauriSnapshot],
        counter: Counter,
    ) -> tuple[dict | None, str]:
        """Return ``(group, skip_reason)``; exactly one is non-empty."""
        template_prop = template.property_by_name(prop.name)
        thesaurus = thesauri_by_id.get(template_prop.thesaurus_id) if template_prop and template_prop.thesaurus_id else None
        if thesaurus is None:
            return None, f"{prop.source} -> {prop.name}: no thesaurus on the Uwazi property"
        if not counter:
            return None, f"{prop.source} -> {prop.name}: no extracted values"
        return {"property": prop, "thesaurus": thesaurus, "counter": counter}, ""

    def _print_skips(self, skips: list[str]) -> None:
        """Print the skipped-field subheading, or nothing when there are none."""
        if not skips:
            return
        SectionPrinter().subheading("Skipped fields (no thesaurus / no extracted values)")
        for reason in skips:
            print(f"    - {reason}")

    def _thesaurus_name_of(self, group: dict) -> str:
        """Resolve the canonical thesaurus name for a group."""
        prop = group["property"]
        return prop.thesaurus or group["thesaurus"].name
