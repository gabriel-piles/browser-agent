"""The verify agent's verdict on what happens after one subtask verification."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VerifyAction = Literal["rewrite_script", "add_extra_script", "re_execute", "accept"]


class VerifyDecision(BaseModel):
    """What the verify agent decided should happen with the subtask's script(s).

    ``rewrite_script`` — the existing script has a fixable logic bug and the
    gap justifies repairing it; ``add_extra_script`` — the uncovered paths
    need mechanics the current script cannot adopt, so a NEW separate script
    must cover them; ``re_execute`` — the script is correct and already
    enumerates the gap; the missing files are due to an interrupted or
    incomplete execution (e.g. a backfill run that died mid-way), not a code
    bug; ``accept`` — the remaining documents are unavailable on the site or
    the gap is too small to justify another build/verify cycle.
    """

    action: VerifyAction = Field(description="One of: rewrite_script, add_extra_script, re_execute, accept.")
    focus: str = Field(
        default="",
        description="For rewrite_script: the concrete fix. For add_extra_script: exactly which paths the extra script must cover. For re_execute: the concrete re-run instruction (which sessions/paths, or re-run the default run to completion). For accept: why the gap is acceptable.",
    )
    reasoning: str = Field(default="", description="Short justification for the action.")
