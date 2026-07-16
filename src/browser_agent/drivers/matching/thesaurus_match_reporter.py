"""Print the human-facing progress + summary lines for one thesaurus.

Hides the per-thesaurus header line, the per-field missing-from-
thesaurus list, and the post-write summary behind one object.
The match driver calls :meth:`print_header` before the LLM
call, :meth:`print_missing_reports` after it, and
:meth:`print_summary` once the YAML has been written.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesaurus_mapping import ThesaurusMapping
from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry


class ThesaurusMatchReporter:
    """Print the thesaurus-level header, missing list, and write summary."""

    def print_header(self, thesaurus_name: str, total: int, exact: int) -> None:
        """Print the thesaurus-level progress line before the LLM call."""
        print(f'  Thesauri "{thesaurus_name}": ' f"{total} distinct value(s) ({exact} exact-matched)")

    def print_missing_reports(self, groups: Iterable[dict]) -> None:
        """Print the per-group missing-from-thesaurus list for every group."""
        for group in groups:
            self._print_missing_report(group)

    def print_summary(
        self,
        out_path,
        mapping_obj: ThesaurusMapping,
        exact_entries: list[ThesaurusMappingEntry],
        llm_entries: list[ThesaurusMappingEntry],
    ) -> None:
        """Print the post-write summary for one thesaurus YAML."""
        needs_review = sum(1 for e in mapping_obj.entries if e.needs_review)
        print(
            f"    Wrote {out_path}  "
            f"({len(mapping_obj.entries)} entries, "
            f"{len(exact_entries)} exact, "
            f"{len(llm_entries)} llm, "
            f"{needs_review} needs review)"
        )

    def _print_missing_report(self, group: dict) -> None:
        """Print the missing-from-thesaurus list for one select/multiselect field."""
        thesaurus: ThesauriSnapshot = group["thesaurus"]
        counter: Counter = group["counter"]
        print(f"\n  Missing from thesaurus {thesaurus.name!r} (need to add on Uwazi side):")
        missing = self._missing_values(group)
        if not missing:
            print("    (none - all extracted values are present on Uwazi)")
            return
        for val in missing:
            print(f"    {val:60s} {counter[val]}")

    def _missing_values(self, group: dict) -> list[str]:
        """Return the distinct extracted values that are not in the thesaurus."""
        thesaurus: ThesauriSnapshot = group["thesaurus"]
        counter: Counter = group["counter"]
        known = {v.strip().casefold() for v in thesaurus.values}
        return sorted(
            (v for v in counter if v.strip().casefold() not in known),
            key=lambda v: (-counter[v], v),
        )
