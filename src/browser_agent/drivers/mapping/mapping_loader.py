"""Load a reviewed ``uwazi_mapping.yaml`` for the apply driver.

Hides the YAML read + :class:`UwaziMapping` validation +
refusal-on-missing plumbing behind one object. The driver
calls :meth:`MappingLoader.load_or_die` and either gets a
:class:`UwaziMapping` or the process exits with a clear
message.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from browser_agent.domain.uwazi_mapping import UwaziMapping


class MappingLoader:
    """Read the active run's mapping YAML, or print a refusal and exit."""

    def load_or_die(self, mapping_path: Path) -> UwaziMapping:
        """Return the loaded mapping, exiting the process when the file is missing."""
        if not mapping_path.exists():
            self._refuse(mapping_path)
        mapping = self._load(mapping_path)
        self._print_summary(mapping_path, mapping)
        return mapping

    def _refuse(self, mapping_path: Path) -> None:
        """Print the missing-mapping refusal and exit the process."""
        print(f"ERROR: No mapping at {mapping_path}.")
        print("  Run step_1_propose_mapping.py first to create one, then review the YAML.")
        sys.exit(1)

    def _load(self, mapping_path: Path) -> UwaziMapping:
        """Parse the YAML file and validate it into a :class:`UwaziMapping`."""
        data = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
        return UwaziMapping.model_validate(data)

    def _print_summary(self, mapping_path: Path, mapping: UwaziMapping) -> None:
        """Print a one-line summary of the loaded mapping."""
        print(f"Loaded mapping from {mapping_path}")
        print(f"  template:     {mapping.template}")
        print(f"  properties:   {len(mapping.properties)}")
        print(f"  sha256:       {mapping.sha256}")
