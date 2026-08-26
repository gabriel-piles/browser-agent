"""Flow orchestrator: plan → review → subtasks → decisions → replan → final verify.

The state machine that drives one full run end-to-end, with circuit breakers
at every level.
"""

from __future__ import annotations

import time

from loguru import logger

_FLOW_RUN_DEADLINE_HOURS = 24
_MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK = 2
_MAX_REPLANS = 2
_MAX_TOTAL_SUBTASK_BUILDS = 20


class ScrapeFlow:
    """Drive the full flow: plan, run subtasks, handle failures, final verify."""

    def __init__(
        self,
        run_path,
        flow_paths,
        state_store,
        planner_factory,
        orchestrator,
        pipeline,
        final_verifier,
        refresh_flow,
    ) -> None:
        self._run_path = run_path
        self._flow_paths = flow_paths
        self._state_store = state_store
        self._planner_factory = planner_factory
        self._orchestrator = orchestrator
        self._pipeline = pipeline
        self._final_verifier = final_verifier
        self._refresh_flow = refresh_flow
        self._start_time = time.monotonic()
        self._total_builds = 0
        self._task = ""
        self._last_logged_progress = None

    async def run(self, task: str) -> int:
        self._start_time = time.monotonic()
        self._task = task
        from browser_agent.use_cases.metadata_db import ensure_metadata_schema

        # The run's metadata.db must exist even when a script saves zero
        # records — verification (reconciler/probe/gap map) opens it read-only.
        ensure_metadata_schema(self._run_path / "metadata.db")
        self._prior_index = self._build_prior_index()
        state = self._state_store.load()

        if state is not None and state.finished:
            logger.info("flow: run already finished — starting refresh pass")
            return await self._refresh_flow.refresh(state, task)
        if state is None:
            logger.info("flow: step 1 of 4 — planning")
            state = await self._plan(task)
            if state is None:
                return 1

        else:
            logger.info("flow: step 2 of 4 — resuming plan {n}", n=state.plan_counter)
            if self._revive_repair_noop(state):
                self._state_store.save(state)

        logger.info("flow: step 3 of 4 — subtask pipeline")
        state = await self._run_subtasks(state)
        if not state.finished:
            return 1

        logger.info("flow: step 4 of 4 — final whole-run verification")
        await self._final_verifier.verify(task)

        state.finished = True
        self._state_store.save(state)
        self._log_plan_progress(state)
        logger.info(
            "flow: step 4 of 4 complete — plan {done}/{total} subtasks",
            done=sum(1 for r in state.records if self._is_terminal(state, r.subtask_id)),
            total=len(state.plan.subtasks),
        )
        logger.info("flow: finished successfully")
        return 0

    async def _plan(self, task: str):
        from browser_agent.domain.orchestrator_state import OrchestratorState

        logger.info("flow: running planner")
        replans = 0
        while replans <= _MAX_REPLANS:
            logger.info(
                "flow: plan attempt {attempt}/{max}",
                attempt=replans + 1,
                max=_MAX_REPLANS + 1,
            )
            planner = self._planner_factory()
            try:
                context = self._plan_context(task, replans)
                plan = await planner.execute(task, context=context)
                if self._discovery_only(plan):
                    replans += 1
                    state = OrchestratorState(plan=plan, plan_counter=replans + 1, replans=replans, records=[])
                    self._state_store.write_plan(state.plan_counter, plan)
                    self._state_store.save(state)
                    logger.warning(
                        'plan has no kind="processing" subtask — a discovery-only plan never '
                        "saves records; replanning ({n}/{max})",
                        n=replans,
                        max=_MAX_REPLANS,
                    )
                    if replans >= _MAX_REPLANS:
                        return state
                    continue
            finally:
                await planner.close()

            state = OrchestratorState(
                plan=plan,
                plan_counter=replans + 1,
                replans=replans,
                records=[],
            )
            self._state_store.write_plan(state.plan_counter, plan)
            self._state_store.save(state)

            summary = self._plan_summary(state)
            decision = await self._orchestrator.decide(summary)
            self._state_store.log_decision(decision, "plan_review")
            logger.info(
                "orchestrator plan review: action={action} reasoning={reasoning}",
                action=decision.action,
                reasoning=decision.reasoning[:120],
            )

            if decision.action == "accept_plan":
                return state
            if decision.action == "replan":
                replans += 1
                state.replans = replans
                if replans >= _MAX_REPLANS:
                    logger.warning("replan cap ({n}) reached — accepting last plan", n=_MAX_REPLANS)
                    return state
                continue
            if decision.action == "abort":
                return None

            return state
        return state

    def _log_plan_progress(self, state) -> None:
        total = len(state.plan.subtasks)
        done = sum(1 for r in state.records if self._is_terminal(state, r.subtask_id))
        if (done, total) == self._last_logged_progress:
            return
        self._last_logged_progress = (done, total)
        logger.info("flow: plan progress {done}/{total} subtasks", done=done, total=total)

    async def _run_subtasks(self, state):

        for spec in state.plan.subtasks:
            self._deadline_check()
            self._log_plan_progress(state)
            logger.info("flow: processing subtask {id}", id=spec.subtask_id)
            if self._total_builds >= _MAX_TOTAL_SUBTASK_BUILDS:
                logger.error(
                    "build cap ({n}) reached — aborting flow. Remaining subtasks: {ids}",
                    n=_MAX_TOTAL_SUBTASK_BUILDS,
                    ids=[s.subtask_id for s in state.plan.subtasks if not self._is_terminal(state, s.subtask_id)],
                )
                return state

            if self._has_failed_dependency(spec, state):
                record = self._find_or_create_record(state, spec.subtask_id)
                record.status = "aborted"
                self._state_store.save(state)
                logger.warning("subtask {id}: dependency failed — aborting", id=spec.subtask_id)
                self._log_plan_progress(state)
                continue

            if self._is_terminal(state, spec.subtask_id):
                logger.info(
                    "subtask {id}: already terminal ({status}) — skipping",
                    id=spec.subtask_id,
                    status=self._get_record(state, spec.subtask_id).status,
                )
                continue

            self._total_builds += 1
            context = self._build_context(state)
            record = await self._pipeline.run(spec, state, context)
            self._state_store.write_report(spec.subtask_id, "subtask", spec)
            self._state_store.save(state)

            if record.status == "succeeded":
                logger.info("subtask {id}: succeeded", id=spec.subtask_id)
                self._log_plan_progress(state)
                continue

            if record.status == "repair_noop":
                logger.warning("subtask {id}: repair_noop — dead end", id=spec.subtask_id)
                decision = await self._orchestrator.decide(self._failure_summary(state, spec.subtask_id))
                self._state_store.log_decision(decision, f"repair_noop:{spec.subtask_id}")
                counter_before = state.plan_counter
                state = await self._apply_decision(decision, state, spec.subtask_id)
                if state.plan_counter != counter_before:
                    return await self._run_subtasks(state)
                self._log_plan_progress(state)
                continue

            if record.repair_decisions >= _MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK:
                logger.warning(
                    "subtask {id}: orchestrator repair cap ({n}) — forcing accept_gap",
                    id=spec.subtask_id,
                    n=_MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK,
                )
                record.status = "accepted_gap"
                self._state_store.log_decision(
                    self._forced_accept_gap(spec.subtask_id),
                    f"repair_cap:{spec.subtask_id}",
                )
                self._log_plan_progress(state)
                continue

            decision = await self._orchestrator.decide(self._failure_summary(state, spec.subtask_id))
            self._state_store.log_decision(decision, f"failure:{spec.subtask_id}")
            counter_before = state.plan_counter
            state = await self._apply_decision(decision, state, spec.subtask_id)
            if state.plan_counter != counter_before:
                return await self._run_subtasks(state)
            self._log_plan_progress(state)

        statuses = {r.subtask_id: r.status for r in state.records}
        if all(statuses.get(s.subtask_id) in ("succeeded", "accepted_gap") for s in state.plan.subtasks):
            state.finished = True
        self._log_plan_progress(state)
        return state

    async def _apply_decision(self, decision, state, subtask_id: str):
        if decision.action == "repair":
            record = self._get_record(state, subtask_id)
            record.repair_decisions += 1
            self._state_store.save(state)
            extra = await self._pipeline.run(
                self._find_spec(state, subtask_id),
                state,
                decision.focus,
            )
            if extra.status == "succeeded":
                return state
            record = self._get_record(state, subtask_id)
            if record.repair_decisions >= _MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK:
                record.status = "accepted_gap"
                self._state_store.save(state)
        elif decision.action == "add_subtask" and state.replans < _MAX_REPLANS:
            focus = f"INCREMENTAL GAP-FILL SUBTASK: Keep all existing subtasks and their scripts intact. Append an incremental subtask targeting: {decision.focus}"
            state = await self._do_replan(state, focus, incremental=True)
        elif decision.action == "replan" and state.replans < _MAX_REPLANS:
            state = await self._do_replan(state, decision.focus)
        elif decision.action in ("accept_gap", "abort"):
            record = self._get_record(state, subtask_id)
            if decision.action == "accept_gap":
                record.status = "accepted_gap"
            self._state_store.save(state)
        return state

    async def _do_replan(self, state, focus: str, incremental: bool = False):
        planner = self._planner_factory()
        try:
            new_plan = await planner.replan(focus, task=self._task, previous_plan=self._replan_context(state))
        finally:
            await planner.close()
        state.replans += 1
        state.plan_counter += 1
        self._state_store.write_plan(state.plan_counter, new_plan)
        self._reset_records(state, new_plan, incremental=incremental)
        self._state_store.save(state)
        logger.info("replan complete — plan {n} (incremental={inc})", n=state.plan_counter, inc=incremental)
        return state

    def _replan_context(self, state) -> str:
        import json

        succeeded = [r.subtask_id for r in state.records if r.status == "succeeded"]
        return json.dumps(
            {
                "previous_plan": state.plan.model_dump(),
                "succeeded_subtask_ids": succeeded,
            },
        )

    def _reset_records(self, state, new_plan, incremental: bool = False) -> None:
        """Keep succeeded (or existing when incremental) records; reset the rest for the new plan."""
        state.plan = new_plan
        for spec in new_plan.subtasks:
            record = self._find_record_by_id(state, spec.subtask_id)
            if record is None:
                continue
            if incremental and record.script_path:
                # Keep existing record intact for incremental gap-fill tasks
                continue
            if record.status != "succeeded":
                record.attempts = 0
                record.repair_decisions = 0
                record.script_hash = ""
                record.status = "pending"

    def _deadline_check(self) -> None:
        elapsed = time.monotonic() - self._start_time
        if elapsed > _FLOW_RUN_DEADLINE_HOURS * 3600:
            raise TimeoutError(f"Flow deadline of {_FLOW_RUN_DEADLINE_HOURS}h exceeded ({elapsed / 3600:.1f}h)")

    def _has_failed_dependency(self, spec, state) -> bool:
        for dep_id in spec.depends_on:
            dep_record = self._get_record(state, dep_id)
            if dep_record is None or dep_record.status not in ("succeeded", "accepted_gap"):
                return True
        return False

    def _revive_repair_noop(self, state) -> bool:
        """Reset dead-end records so a resumed run re-attempts them.

        ``repair_noop`` means the builder produced identical code after a
        repair turn — a dead end the live flow resolves through the
        orchestrator, never a terminal outcome. A record persisted in that
        state would otherwise be skipped on resume and stall the plan.
        """
        changed = False
        for record in state.records:
            if record.status == "repair_noop":
                record.status = "pending"
                changed = True
        return changed

    def _is_terminal(self, state, subtask_id: str) -> bool:
        record = self._get_record(state, subtask_id)
        if record is None:
            return False
        return record.status in ("succeeded", "accepted_gap", "repair_noop")

    def _get_record(self, state, subtask_id: str):
        for r in state.records:
            if r.subtask_id == subtask_id:
                return r
        return None

    def _find_or_create_record(self, state, subtask_id: str):
        from browser_agent.domain.subtask_record import SubtaskRecord

        for r in state.records:
            if r.subtask_id == subtask_id:
                return r
        record = SubtaskRecord(subtask_id=subtask_id, plan_index=state.plan_counter)
        state.records.append(record)
        return record

    def _find_record_by_id(self, state, subtask_id: str):
        return self._get_record(state, subtask_id)

    def _find_spec(self, state, subtask_id: str):
        for s in state.plan.subtasks:
            if s.subtask_id == subtask_id:
                return s
        raise ValueError(f"subtask {subtask_id} not found in plan")

    @staticmethod
    def _discovery_only(plan) -> bool:
        """True when a plan collects links but never processes them.

        A plan whose subtasks are all ``kind="discovery"`` can never
        produce ``save_record`` rows, yet every subtask succeeds and the
        flow reports finished with an empty metadata table. Deterministic
        guard: force a replan that adds a processing subtask.
        """
        subtasks = plan.subtasks
        return bool(subtasks) and all(s.kind == "discovery" for s in subtasks)

    def _plan_summary(self, state) -> str:
        import json

        return json.dumps(
            {
                "plan_counter": state.plan_counter,
                "replans": state.replans,
                "task_summary": state.plan.task_summary,
                "site_overview": state.plan.site_overview[:500],
                "subtask_count": len(state.plan.subtasks),
                "subtask_ids": [s.subtask_id for s in state.plan.subtasks],
            },
            indent=2,
        )

    def _verification_digest(self, subtask_id: str) -> dict:
        import json

        report_path = self._run_path / "flow" / "subtasks" / subtask_id / "verification_report.json"
        if not report_path.exists():
            return {}
        try:
            report = json.loads(report_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return {
            "coverage_complete": report.get("coverage_complete"),
            "missing_count": report.get("missing_count"),
            "expected_pdf_total": report.get("expected_pdf_total"),
            "observed_pdf_total": report.get("observed_pdf_total"),
            "overall_assessment": report.get("overall_assessment", "")[:400],
            "probe_verdicts": [
                f"{r.get('verdict')}: {r.get('source_url', '')[:80]}" for r in report.get("probe_results", [])
            ],
        }

    def _failure_summary(self, state, subtask_id: str) -> str:
        import json

        record = self._get_record(state, subtask_id)
        return json.dumps(
            {
                "state": {
                    "plan_counter": state.plan_counter,
                    "replans": state.replans,
                    "replans_remaining": _MAX_REPLANS - state.replans,
                },
                "failed_subtask": {
                    "subtask_id": subtask_id,
                    "status": record.status if record else "unknown",
                    "repair_decisions": record.repair_decisions if record else 0,
                    "repairs_remaining": _MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK - (record.repair_decisions if record else 0),
                    "attempts": record.attempts if record else 0,
                    "verification": self._verification_digest(subtask_id),
                },
                "circuit_breakers": {
                    "repairs_per_subtask_cap": _MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK,
                    "replans_cap": _MAX_REPLANS,
                    "builds_cap": _MAX_TOTAL_SUBTASK_BUILDS,
                    "builds_used": self._total_builds,
                },
            },
            indent=2,
        )

    def _forced_accept_gap(self, subtask_id: str):
        from browser_agent.domain.orchestrator_decision import OrchestratorDecision

        return OrchestratorDecision(
            action="accept_gap",
            subtask_id=subtask_id,
            focus="",
            reasoning=f"Repair cap ({_MAX_ORCHESTRATOR_REPAIRS_PER_SUBTASK}) reached for subtask {subtask_id}",
        )

    def _build_prior_index(self):
        from browser_agent.use_cases.prior_scripts_index import PriorScriptsIndex

        return PriorScriptsIndex(current_run_path=self._run_path)

    def _plan_context(self, task: str, replans: int) -> str:
        parts: list[str] = []
        if replans > 0:
            parts.append("replan focus")
        if hasattr(self, "_prior_index") and self._prior_index is not None:
            prior = self._prior_index.find_relevant(task, max_results=5)
            if prior:
                parts.append(self._prior_index.render_context(prior))
        return "\n\n".join(parts)

    def _build_context(self, state) -> str:
        parts: list[str] = []
        # Sibling scripts from current run
        sibling_parts: list[str] = []
        for r in state.records:
            if r.status == "succeeded" and r.script_path:
                sibling_parts.append(f"- {r.subtask_id}: {r.script_path} ({r.status})")
        if sibling_parts:
            parts.append("Sibling scripts already emitted:\n" + "\n".join(sibling_parts))
        # Prior scripts from other runs (matching kind)
        if hasattr(self, "_prior_index") and self._prior_index is not None:
            task = state.plan.task_summary
            prior = self._prior_index.find_relevant(task, max_results=3)
            if prior:
                parts.append(self._prior_index.render_context(prior))
        return "\n\n".join(parts)
