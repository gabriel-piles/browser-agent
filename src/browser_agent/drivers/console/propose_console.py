"""Console reporting for the step_1 propose driver.

Keeps the human-facing print logic (catalog summary, mapping body,
notices) behind one object so the driver script stays a thin flow.
"""

from __future__ import annotations

from pathlib import Path

from browser_agent.domain.metadata_field_catalog import MetadataFieldCatalog
from browser_agent.domain.uwazi_mapping import UwaziMapping


class ProposeConsole:
    """Print the human-facing progress + result for the propose driver."""

    def __init__(self, mapping_path: Path) -> None:
        self._mapping_path = mapping_path

    def print_catalog(self, catalog: MetadataFieldCatalog, total_rows: int) -> None:
        """Print a one-line summary of the catalog built from the database."""
        sample = [f.name for f in catalog.fields[:8]]
        suffix = "…" if len(catalog.fields) > 8 else ""
        print(f"Loaded {total_rows} row(s) from metadata.db")
        print(f"  distinct fields: {len(catalog.fields)}")
        print(f"  sample fields:   {sample}{suffix}")

    def print_no_db(self, db_path: Path) -> None:
        """Print the message when the cache file is missing."""
        print(f"No metadata.db at {db_path}. Run the scraper first.")

    def print_no_rows(self) -> None:
        """Print the message when the cache is empty."""
        print("No rows in metadata.db. Run the scraper first.")

    def print_mapping(self, mapping: UwaziMapping) -> None:
        """Print the full mapping summary, then the next-step hint."""
        print(f"\nDraft mapping written to {self._mapping_path}")
        self._print_body(mapping)
        print("\nNext: review the YAML, then run step_2_uwazi_match.py " "and step_3_uwazi_apply.py.")

    def _print_body(self, mapping: UwaziMapping) -> None:
        """Print counts, skipped entries, and the identity block."""
        print(f"  template:       {mapping.template}")
        print(f"  properties:     {len(mapping.properties)}")
        print(f"  skipped:        {len(mapping.skipped)}")
        self._print_skipped(mapping)
        self._print_identity(mapping.identity)

    def _print_skipped(self, mapping: UwaziMapping) -> None:
        """Print each skipped-field entry under the mapping body."""
        for entry in mapping.skipped:
            print(f"    - {entry.source} :: {entry.reason} :: {entry.notes or ''}")

    def _print_identity(self, identity) -> None:
        """Print the identity block of the mapping."""
        print(
            f"  identity:       key_source={identity.key_source.value} "
            f"key_property={identity.key_property!r} "
            f"path_placeholder={identity.path_placeholder!r} "
            f"source_url_property={identity.source_url_property!r}"
        )
