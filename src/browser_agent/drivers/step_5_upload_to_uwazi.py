"""Apply the reviewed mapping + thesaurus mappings to Uwazi (no LLM).

Run this driver after :mod:`browser_agent.drivers.step_3_propose_mapping`
(LLM draft) and :mod:`browser_agent.drivers.step_4_validate_data`
(thesaurus value mappings), and after a human has reviewed the
YAMLs they wrote. The driver reads the reviewed
``mappings/uwazi_mapping.yaml``, loads the ``metadata.db`` rows for
the splits named by ``active_flow`` in ``active_run.yaml`` (the same
selector step 1 uses), builds a :class:`SyncPlan` via the no-LLM
:func:`apply_mapping_use_case.execute`, applies thesaurus
value substitution, and pushes the result to Uwazi via
:func:`apply_mapping_use_case.push_plan`.

No LLM is called on this path: records come from the
``metadata.db`` cache written by the scraper, select /
multiselect values are substituted with their canonical Uwazi
form using the ``thesauri_mappings/*.yaml`` files, and the
resulting entities are created (or updated) on Uwazi.

The driver does not currently run a dry-run by default — it
pushes to Uwazi. The operator edits ``PUSH`` in
``active_run.yaml`` to switch to read-only.
"""

from __future__ import annotations

from browser_agent.adapters.runs_config_loader import RunsConfigLoader
from browser_agent.drivers.apply.apply_plan_builder import ApplyPlanBuilder
from browser_agent.drivers.apply.apply_plan_executor import ApplyPlanExecutor
from browser_agent.drivers.apply.apply_result_printer import ApplyResultPrinter
from browser_agent.drivers.mapping.mapping_loader import MappingLoader
from browser_agent.drivers.paths.run_paths import RunPaths
from browser_agent.drivers.flow.active_flow_parser import parse_active_flow
from browser_agent.drivers.flow.active_flow_source import load_active_flow_raw
from browser_agent.drivers.flow.flow_task_slug_resolver import resolve_flow_task_slugs
from browser_agent.drivers.clients.uwazi_client_factory import UwaziClientFactory

MAX_ENTITIES_TO_UPLOAD = 300000


class ApplyDriver:
    """End-to-end driver: read mapping -> build plan -> push (or dry-run)."""

    def __init__(self) -> None:
        self._paths = RunPaths()
        self._uwazi = UwaziClientFactory()
        self._loader = MappingLoader()
        self._printer = ApplyResultPrinter()

    def run(self) -> None:
        """Module entry: load the run, build the plan, push to Uwazi."""
        run_config = RunsConfigLoader.load_run_config_from_run()
        mapping = self._loader.load_or_die(self._paths.default_mapping_path())
        client = self._uwazi.build()
        plan = self._build_plan(client, mapping, run_config)
        if len(plan.rows) > MAX_ENTITIES_TO_UPLOAD:
            plan = plan.model_copy(update={"rows": plan.rows[:MAX_ENTITIES_TO_UPLOAD]})
            print(f"Apply capped to the first {MAX_ENTITIES_TO_UPLOAD} plan rows.")
        self._printer.print_plan_rows(plan)
        self._printer.print_plan_counts(plan)
        if not plan.rows:
            print("Apply stopped: no plan rows to push.")
            return
        result = self._apply(plan, run_config.push, client)
        self._printer.print_apply_result(result)

    def _task_slugs_for_active_flow(self) -> frozenset[str]:
        """Resolve the active_flow split selection into metadata task slugs.

        Raises :class:`ActiveFlowError` when active_flow is unset — the
        same strictness step 1 applies, so uploads always match the
        splits the run actually executed.
        """
        run_path = self._paths.run_path()
        selection = parse_active_flow(load_active_flow_raw())
        slugs = resolve_flow_task_slugs(run_path, selection)
        print(f"active_flow {selection.describe()} resolved task slugs: {', '.join(sorted(slugs))}")
        return slugs

    def _build_plan(self, client, mapping, run_config):
        """Build the :class:`SyncPlan` for the run's metadata.db rows."""
        builder = ApplyPlanBuilder(
            metadata_db_path=self._paths.metadata_db_path(),
            thesauri_mappings_dir=self._paths.thesauri_mappings_dir(),
            downloads_dir=self._paths.downloads_dir(),
            task_slugs=self._task_slugs_for_active_flow(),
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
