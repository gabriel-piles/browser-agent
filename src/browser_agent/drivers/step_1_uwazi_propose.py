"""Propose a Uwazi mapping from the active run's ``metadata.db``.

Run this driver when you have a populated ``data/runs/<active_run>/metadata.db``
and want an LLM to draft a Uwazi mapping from the actual extracted
data, without the intermediate analyzer step. The human reviews the
resulting YAML in ``data/runs/<active_run>/mappings/`` and edits it
before running :mod:`browser_agent.drivers.step_2_uwazi_match` and
::mod:`browser_agent.drivers.step_3_uwazi_apply`.

The driver does not mutate the ``metadata.db`` cache and does not
push anything to Uwazi. The only side effect is the YAML file; the
human is the gate.
"""

from __future__ import annotations

import asyncio

from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.drivers.console.propose_console import ProposeConsole
from browser_agent.drivers.paths.run_paths import RunPaths
from browser_agent.drivers.clients.uwazi_client_factory import UwaziClientFactory
from browser_agent.domain.run_config import RunConfig
from browser_agent.use_cases.metadata_catalog_builder import MetadataCatalogBuilder
from browser_agent.use_cases.propose_mapping_use_case import ProposeMappingUseCase


class ProposeDriver:
    """End-to-end driver: read DB -> ask LLM -> write draft mapping YAML."""

    def __init__(self) -> None:
        self._paths = RunPaths()
        self._uwazi = UwaziClientFactory()
        self._console = ProposeConsole(self._paths.default_mapping_path())

    def run(self) -> None:
        """Module entry: run the async driver."""
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        """Load the run, build the catalog, ask the LLM, print the result."""
        run_config = RunsConfigLoader.load_active()
        if not self._has_template(run_config):
            return
        db_path = self._paths.metadata_db_path()
        if not db_path.exists():
            self._console.print_no_db(db_path)
            return
        catalog, total_rows = MetadataCatalogBuilder(run_config).build(db_path)
        if catalog is None:
            self._console.print_no_rows()
            return
        self._console.print_catalog(catalog, total_rows)
        mapping = await self._propose(run_config, catalog)
        self._console.print_mapping(mapping)

    def _has_template(self, run_config: RunConfig) -> bool:
        """Print a refusal and return False when the run has no Uwazi template set."""
        if run_config.template:
            return True
        print(f"Run {run_config.name!r} has no 'template' set in runs.yaml; cannot propose.")
        return False

    async def _propose(self, run_config: RunConfig, catalog):
        """Build the use case and call ``propose_with_catalog`` for the active run."""
        use_case = ProposeMappingUseCase(
            client=self._uwazi.build(),
            llm=OllamaAdapter(),
        )
        return await use_case.propose_with_catalog(
            template_name=run_config.template,
            catalog=catalog,
            output_path=self._paths.default_mapping_path(),
        )


def main() -> None:
    """Module entry: invoke the propose driver."""
    ProposeDriver().run()


if __name__ == "__main__":
    main()
