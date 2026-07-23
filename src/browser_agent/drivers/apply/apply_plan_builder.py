"""Build a :class:`SyncPlan` from a :class:`UwaziMapping` + the live ``metadata.db``.

Hides the no-LLM ``apply_mapping_use_case.execute`` call behind
one object so the apply driver does not have to wire the
:class:`UwaziClient`, the thesaurus mappings directory, and the
``run_filter`` together. The driver injects a
:class:`UwaziClientFactory` so it can construct the same client
later for the actual push.
"""

from __future__ import annotations

from browser_agent.domain.run_config import RunConfig
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.use_cases.sync_plan_builder import execute as build_plan
from uwazi_api.client import UwaziClient


class ApplyPlanBuilder:
    """Build a :class:`SyncPlan` for one mapping against the live metadata.db."""

    def __init__(self, client: UwaziClient, metadata_db_path, thesauri_mappings_dir, downloads_dir=None) -> None:
        self._client = client
        self._metadata_db_path = metadata_db_path
        self._thesauri_mappings_dir = thesauri_mappings_dir
        self._downloads_dir = downloads_dir

    def build(self, mapping: UwaziMapping, run_config: RunConfig):
        """Return the :class:`SyncPlan` for the run's metadata.db rows."""
        return build_plan(
            mapping=mapping,
            metadata_db_path=self._metadata_db_path,
            client=self._client,
            thesauri_mappings_dir=self._thesauri_mappings_dir,
            run=run_config.run_filter,
            downloads_dir=self._downloads_dir,
        )
