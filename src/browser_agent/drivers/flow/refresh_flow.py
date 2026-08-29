"""Refresh pass over an already-finished run: retry failed downloads, pick up new documents.

Re-runs discovery scripts (the new-document detection step — the
``discovered_links`` diff is the content-change signal), gathers a
deterministic assessment, and lets the orchestrator judge whether to
re-execute existing emitted scripts. No LLM rebuild, no repair loop:
one shot per refresh pass, evidence left on disk for the next re-run.
"""

from __future__ import annotations

from loguru import logger

from browser_agent.use_cases.refresh_assessment_builder import RefreshAssessmentBuilder

_MAX_FAILED_DOCS_IN_SUMMARY = 40
_MAX_NEW_LINKS_IN_SUMMARY = 50
_DESCRIPTION_CHARS = 200


class RefreshFlow:
    """Drive one refresh pass over a finished flow state."""

    def __init__(self, run_path, state_store, pipeline, orchestrator, final_verifier) -> None:
        self._run_path = run_path
        self._state_store = state_store
        self._pipeline = pipeline
        self._orchestrator = orchestrator
        self._final_verifier = final_verifier
        self._db_path = run_path / "metadata.db"
        self._downloads_path = run_path / "downloads"

    async def refresh(self, state, task: str) -> int:
        """One refresh pass: re-run discovery, assess, decide, apply."""
        done = await self._reexecute_discovery(state)
        assessment = RefreshAssessmentBuilder(self._db_path, self._downloads_path).build()
        if not assessment.failed_documents and not assessment.new_discovered_links:
            logger.info("refresh: up to date — no failed downloads, no new documents")
            return 0
        summary = self._summary(state, assessment)
        decision = await self._orchestrator.decide(summary)
        self._state_store.log_decision(decision, "refresh", summary)
        logger.info(
            "orchestrator refresh: action={action} reasoning={reasoning}",
            action=decision.action,
            reasoning=decision.reasoning[:120],
        )
        return await self._apply(decision, state, task, done)

    async def _reexecute_discovery(self, state) -> set[str]:
        """Re-run every discovery subtask that has a script — new-document detection."""
        done: set[str] = set()
        for spec in state.plan.subtasks:
            if spec.kind != "discovery":
                continue
            record = next((r for r in state.records if r.subtask_id == spec.subtask_id), None)
            if record is None or not record.script_path:
                continue
            logger.info("refresh: re-running discovery script {id}", id=spec.subtask_id)
            await self._pipeline.reexecute(spec, state)
            self._state_store.save(state)
            done.add(spec.subtask_id)
        return done

    async def _apply(self, decision, state, task: str, done: set[str]) -> int:
        """Apply the orchestrator's refresh decision; return the flow exit code."""
        if decision.action == "refresh":
            return await self._reexecute_subtasks(decision, state, task, done)
        if decision.action in ("accept_gap", "finish"):
            logger.info("refresh: {action} — {reasoning}", action=decision.action, reasoning=decision.reasoning[:200])
            return 0
        if decision.action == "abort":
            return 1
        logger.warning("refresh: action {action} not allowed for a refresh summary", action=decision.action)
        return 1

    async def _reexecute_subtasks(self, decision, state, task: str, done: set[str]) -> int:
        """Re-execute the decision's subtasks in plan order, then final-verify."""
        for spec in state.plan.subtasks:
            if spec.subtask_id not in decision.subtask_ids or spec.subtask_id in done:
                continue
            logger.info("refresh: re-executing subtask {id}", id=spec.subtask_id)
            await self._pipeline.reexecute(spec, state)
            self._state_store.save(state)
        known = {s.subtask_id for s in state.plan.subtasks}
        for sid in set(decision.subtask_ids) - known:
            logger.warning("refresh: unknown subtask id {id} — skipping", id=sid)
        return await self._finalize_refresh(state, task)

    async def _finalize_refresh(self, state, task: str) -> int:
        """Re-assess, final-verify, and persist the finished state."""
        assessment = RefreshAssessmentBuilder(self._db_path, self._downloads_path).build()
        logger.info("refresh complete — {n} document(s) still failing", n=len(assessment.failed_documents))
        await self._final_verifier.verify(task)
        state.finished = True
        self._state_store.save(state)
        return 0

    def _summary(self, state, assessment) -> str:
        """Render the refresh summary JSON shown to the orchestrator."""
        import json

        return json.dumps(self._summary_payload(state, assessment), indent=2)

    def _summary_payload(self, state, assessment) -> dict:
        """The refresh summary structure (semantics documented in the prompt)."""
        return {
            "kind": "refresh",
            "failed_documents": [f.model_dump() for f in assessment.failed_documents[:_MAX_FAILED_DOCS_IN_SUMMARY]],
            "failed_count": len(assessment.failed_documents),
            "new_discovered_links": [n.model_dump() for n in assessment.new_discovered_links[:_MAX_NEW_LINKS_IN_SUMMARY]],
            "new_link_count": len(assessment.new_discovered_links),
            "subtasks": self._subtask_entries(state),
            "verification": self._verification_entries(assessment),
        }

    def _verification_entries(self, assessment) -> dict:
        """Verification digests keyed by each failed document's subtask."""
        ids = {f.subtask_id for f in assessment.failed_documents if f.subtask_id}
        return {sid: self._digest(sid) for sid in ids}

    def _subtask_entries(self, state) -> list[dict]:
        """One summary entry per plan subtask: status, script presence, description."""
        return [
            {
                "subtask_id": s.subtask_id,
                "kind": s.kind,
                "status": next((r.status for r in state.records if r.subtask_id == s.subtask_id), "unknown"),
                "has_script": any(r.script_path for r in state.records if r.subtask_id == s.subtask_id),
                "description": s.description[:_DESCRIPTION_CHARS],
            }
            for s in state.plan.subtasks
        ]

    def _digest(self, subtask_id: str) -> dict[str, object]:
        """Compact verification digest for one subtask; ``{}`` when unreadable."""
        import json

        path = self._run_path / "flow" / "subtasks" / subtask_id / "verification_report.json"
        try:
            report = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return {
            "missing_count": report.get("missing_count"),
            "expected_pdf_total": report.get("expected_pdf_total"),
            "observed_pdf_total": report.get("observed_pdf_total"),
            "overall_assessment": report.get("overall_assessment", "")[:400],
        }
