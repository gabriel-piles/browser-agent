"""VerificationReport extended with the verify agent's next-step decision."""

from __future__ import annotations

from pydantic import Field

from browser_agent.domain.verify_decision import VerifyDecision
from browser_agent.domain.verification_report import VerificationReport


class FlowVerificationReport(VerificationReport):
    """The legacy whole-run verification report plus the flow decision.

    The legacy fields drive the deterministic pass/fail gates unchanged
    (:meth:`SubtaskVerifierUseCase.passed`); ``decision`` is the verify
    agent's verdict that the deterministic orchestrator acts on.
    """

    decision: VerifyDecision = Field(
        description="The verify agent's verdict: rewrite_script, add_extra_script, or accept.",
    )
