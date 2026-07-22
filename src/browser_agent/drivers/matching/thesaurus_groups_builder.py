"""Build the per-thesaurus groups the match driver iterates over.

Hides the per-field select/multiselect filtering, the thesaurus
lookup for each field's target property, the bucketing-by-thesaurus
step, and the cross-field counter merge behind one object. The
match driver calls :meth:`build` once for the mapping and
:meth:`bucket_by_thesaurus` to group the resulting groups.

Every select/multiselect property in the mapping produces one
group, even when the property carries only a constant
``default_value`` and no extracted column: the default tokens
are added to the group's counter so the per-thesaurus mapping
YAML still gets written. ``step_3_upload_to_uwazi.py`` then
substitutes the default through the same lookup the extracted
values use.
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
        """Return one group dict per select/multiselect property with values to map."""
        groups: list[dict] = []
        skips: list[str] = []
        for prop in mapping.properties:
            if prop.type not in (FieldType.SELECT, FieldType.MULTI_SELECT):
                continue
            extracted = field_counters.get(prop.source, Counter()) if prop.source else Counter()
            counter = self._merge_with_default(extracted, prop)
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

    def _merge_with_default(self, extracted: Counter, prop) -> Counter:
        """Return ``extracted`` augmented with ``prop.default_value`` tokens.

        Default tokens are added with a count of 1 so the
        exact-matcher / LLM still see them; an extracted counter
        that already holds the same label is left untouched (the
        higher extracted count wins). Properties without a
        ``default_value`` get the original counter back unchanged.
        """
        tokens = self._default_tokens(prop)
        if not tokens:
            return extracted
        merged = Counter(extracted)
        for token in tokens:
            merged[token] = max(merged.get(token, 0), 1)
        return merged

    def _default_tokens(self, prop) -> list[str]:
        """Split ``prop.default_value`` into individual tokens for select/multiselect."""
        if prop.default_value is None:
            return []
        return [token for token in (t.strip() for t in str(prop.default_value).split(",")) if token]

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
            return None, f"{prop.source} -> {prop.name}: no extracted values and no default_value"
        return {"property": prop, "thesaurus": thesaurus, "counter": counter}, ""

    def _print_skips(self, skips: list[str]) -> None:
        """Print the skipped-field subheading, or nothing when there are none."""
        if not skips:
            return
        SectionPrinter().subheading("Skipped fields (no thesaurus / no extracted values / no default)")
        for reason in skips:
            print(f"    - {reason}")

    def _thesaurus_name_of(self, group: dict) -> str:
        """Resolve the canonical thesaurus name for a group."""
        prop = group["property"]
        return prop.thesaurus or group["thesaurus"].name
