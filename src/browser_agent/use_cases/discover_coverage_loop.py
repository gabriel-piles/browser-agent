"""Drive incremental discover passes until the plan reports full coverage.

The discoverer's LLM/explore budget can run out mid-task on large sites
(e.g. one pass verifies only sessions 12-32 of 2-63). Each pass emits
splits for the ranges it verified and reports ``coverage_complete=false``
plus notes about the remainder; the driver reruns the discoverer with
the accumulated splits as context until coverage completes.

A pass that verifies pages already inside an existing split's dynamic
scope makes progress WITHOUT emitting splits; only a pass whose notes
repeat the previous pass's notes verbatim is a true stall.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from browser_agent.configuration import DISCOVER_MAX_PASSES
from browser_agent.domain.discover_plan import DiscoverPlan
from browser_agent.domain.task_split import TaskSplit


class DiscoverCoverageLoop:
    """Run discover passes until ``coverage_complete`` or the pass cap."""

    def __init__(
        self,
        run_pass: Callable[[], Awaitable[DiscoverPlan]],
        write_pass: Callable[[DiscoverPlan], list[str]],
    ) -> None:
        self._run_pass: Callable[[], Awaitable[DiscoverPlan]] = run_pass
        self._write_pass: Callable[[DiscoverPlan], list[str]] = write_pass

    async def run(self, existing: list[TaskSplit]) -> int:
        """Run passes; return the process exit code."""
        previous_notes = ""
        total_created: list[str] = []
        for attempt in range(1, DISCOVER_MAX_PASSES + 1):
            plan = await self._run_pass()
            created = self._write_pass(plan)
            total_created.extend(created)
            logger.info(
                "discover pass={a}/{m} coverage_complete={c} created={n}",
                a=attempt,
                m=DISCOVER_MAX_PASSES,
                c=plan.coverage_complete,
                n=created,
            )
            if plan.coverage_complete:
                return self._exit_code(existing, total_created)
            if not created and plan.discoverer_notes == previous_notes:
                self._log_stalled(plan)
                return 1
            previous_notes = plan.discoverer_notes
        logger.error(
            "coverage still incomplete after {m} discover passes — rerun the same command to resume",
            m=DISCOVER_MAX_PASSES,
        )
        return 1

    @staticmethod
    def _exit_code(existing: list[TaskSplit], created: list[str]) -> int:
        if not existing and not created:
            logger.error("first discover pass produced no splits — task could not be split")
            return 1
        return 0

    @staticmethod
    def _log_stalled(plan: DiscoverPlan) -> None:
        logger.error(
            "discover pass emitted no splits and its notes repeat the previous "
            "pass's notes verbatim — coverage cannot advance (notes: {n})",
            n=plan.discoverer_notes,
        )
