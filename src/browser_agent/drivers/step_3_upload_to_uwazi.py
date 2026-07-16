"""Apply the reviewed mapping + thesaurus mappings to Uwazi (no LLM).

Run this driver after :mod:`browser_agent.drivers.step_1_uwazi_propose`
(LLM draft) and :mod:`browser_agent.drivers.step_2_uwazi_match`
(thesaurus value mappings), and after a human has reviewed the
YAMLs they wrote. The driver reads the reviewed
``mappings/uwazi_mapping.yaml``, loads the ``metadata.db`` rows
for the active run, builds a :class:`SyncPlan` via the no-LLM
:class:`apply_mapping_use_case.execute`, applies thesaurus
value substitution, and pushes the result to Uwazi via
:func:`apply_mapping_use_case.push_plan`.

No LLM is called on this path: records come from the
``metadata.db`` cache written by the scraper, select /
multiselect values are substituted with their canonical Uwazi
form using the ``thesauri_mappings/*.yaml`` files, and the
resulting entities are created (or updated) on Uwazi.

The driver does not currently run a dry-run by default — it
pushes to Uwazi. The operator edits ``PUSH`` in
``runs.yaml`` to switch to read-only.
"""

from __future__ import annotations

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.drivers.apply.apply_plan_builder import ApplyPlanBuilder
from browser_agent.drivers.apply.apply_plan_executor import ApplyPlanExecutor
from browser_agent.drivers.apply.apply_result_printer import ApplyResultPrinter
from browser_agent.drivers.mapping.mapping_loader import MappingLoader
from browser_agent.drivers.paths.run_paths import RunPaths
from browser_agent.drivers.clients.uwazi_client_factory import UwaziClientFactory


class ApplyDriver:
    """End-to-end driver: read mapping -> build plan -> push (or dry-run)."""

    def __init__(self) -> None:
        self._paths = RunPaths()
        self._uwazi = UwaziClientFactory()
        self._loader = MappingLoader()
        self._printer = ApplyResultPrinter()

    def run(self) -> None:
        """Module entry: load the run, build the plan, push to Uwazi."""
        run_config = RunsConfigLoader.load_active()
        mapping = self._loader.load_or_die(self._paths.default_mapping_path())
        client = self._uwazi.build()
        plan = self._build_plan(client, mapping, run_config)
        self._printer.print_plan_rows(plan)
        self._printer.print_plan_counts(plan)
        if not plan.rows:
            print("Apply stopped: no plan rows to push.")
            return
        result = self._apply(plan, run_config.push, client)
        self._printer.print_apply_result(result)

    def _build_plan(self, client, mapping, run_config):
        """Build the :class:`SyncPlan` for the run's metadata.db rows."""
        builder = ApplyPlanBuilder(
            client=client,
            metadata_db_path=self._paths.metadata_db_path(),
            thesauri_mappings_dir=self._paths.thesauri_mappings_dir(),
            downloads_dir=self._paths.downloads_dir(),
        )
        return builder.build(mapping, run_config)

    def _apply(self, plan, push: bool, client):
        """Push the plan to Uwazi (or dry-run) and return the apply result."""
        return ApplyPlanExecutor(client).execute(plan, push)


def main() -> None:
    """Module entry: invoke the apply driver."""
    ApplyDriver().run()


if __name__ == "__main__":
    main()
