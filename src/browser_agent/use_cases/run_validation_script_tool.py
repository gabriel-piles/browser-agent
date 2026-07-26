"""The ``run_validation_script`` tool bound to the Pydantic-AI agent.

The tool takes a self-contained Python script (the same shape as the
final deliverable), runs it in a subprocess via the injected
:class:`ScriptRunnerPort`, and returns the exit code + combined
stdout/stderr. The agent uses this to validate its selectors, scroll
loops and filter logic *before* producing the final script.

A hard counter on :class:`AgentDeps` caps how many validation runs
one agent turn may perform (``MAX_VALIDATION_ATTEMPTS``). The system
prompt asks for "max 3" but LLMs routinely ignore prose limits and
loop until the request budget is exhausted; this counter is the
backstop that forces the agent to emit a final script instead of
retrying forever.
"""

from __future__ import annotations

import re

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.script_runner_port import ScriptRunnerPort
from browser_agent.use_cases.agent_deps import AgentDeps

VALIDATION_TIMEOUT_S = 90.0
_ERROR_HEAD_CHARS = 2000
_TIMEOUT_NOTICE_RE = re.compile(r"\[TIMEOUT[^\]]*\]")


async def run_validation_script(ctx: RunContext[AgentDeps], python_code: str) -> str:
    """Run ``python_code`` in a subprocess and return the result.

    Use this tool to TEST a single script that proves your FULL
    strategy — navigate to the target URL, find the key selectors,
    click ONE filter, scroll ONCE, and print what it discovers
    (element counts, text, hrefs) — all in the same script. Pack
    every check you need into ONE script so you don't waste attempts.
    If the validation script fails, read the error output, fix your
    approach, and re-run. Only emit the final :class:`GeneratedScript`
    once a validation script succeeds.

    The script must be self-contained (imports its own dependencies,
    uses zendriver, ``asyncio.run(main())``) — exactly like the final
    deliverable.

    You have a HARD limit of ``validation_limit`` attempts per turn.
    When the limit is reached the tool refuses to run and tells you
    to emit the best script you can from the exploration you already
    did — do NOT keep retrying.
    """
    deps = ctx.deps
    if deps.validation_attempts >= deps.validation_limit:
        return _limit_reached(deps)
    deps.validation_attempts += 1
    runner: ScriptRunnerPort = deps.script_runner
    async with traced_tool("run_validation_script"):
        result: ScriptExecutionResult = await runner.run(python_code, timeout=VALIDATION_TIMEOUT_S)
    if _is_pure_timeout(result):
        deps.validation_attempts -= 1
        return _timeout_no_charge(result, deps.validation_attempts, deps.validation_limit)
    return _format_result(result, deps.validation_attempts, deps.validation_limit)


def _is_pure_timeout(result: ScriptExecutionResult) -> bool:
    """True when a timeout produced no partial diagnostics before the notice."""
    if result.exit_code != 124:
        return False
    stripped = _TIMEOUT_NOTICE_RE.sub("", result.output)
    return not stripped.strip()


def _limit_reached(deps: AgentDeps) -> str:
    return (
        f"# Validation limit reached ({deps.validation_limit}/{deps.validation_limit}).\n"
        "You have used all your validation attempts. STOP calling this tool.\n"
        "Emit the final GeneratedScript now using the selectors and patterns\n"
        "you verified during exploration. Do not call run_validation_script again."
    )


def _format_result(result: ScriptExecutionResult, attempt: int, limit: int) -> str:
    status = "SUCCESS" if result.success else f"FAILED (exit_code={result.exit_code})"
    header = f"# Validation attempt {attempt}/{limit}: {status}"
    body = result.output if result.success else _extract_error(result.output)
    remaining = limit - attempt
    footer = (
        f"\n# You have {remaining} validation attempt(s) remaining."
        if remaining > 0
        else "\n# This was your LAST validation attempt. Emit the final script now."
    )
    return f"{header}\n\n{body}{footer}"


def _timeout_no_charge(result: ScriptExecutionResult, attempt: int, limit: int) -> str:
    """Format a timeout that did NOT consume an attempt."""
    remaining = limit - attempt
    header = f"# Validation TIMEOUT (not charged; {remaining} attempt(s) still remaining)"
    note = (
        "The script produced no output before the timeout — likely a slow\n"
        "target site, not a strategy error. Re-run the same script; the\n"
        f"timeout was {VALIDATION_TIMEOUT_S:.0f}s. If it times out again,\n"
        "simplify the script (fewer navigations, skip PDF downloads)."
    )
    return f"{header}\n\n{result.output}\n\n{note}"


def _extract_error(output: str) -> str:
    """Keep the printed diagnostics (head) AND the last traceback.

    Step 7 is built around the prints — counts, sample hrefs,
    label-vs-badge comparisons. A script that proved 8 of 9 checks
    and crashed on the 9th must not lose the evidence of what
    already worked. We keep the head (everything before the last
    traceback) capped to ``_ERROR_HEAD_CHARS``, then the last
    ``Traceback`` block (inclusive), with a marker between them.
    """
    marker = "Traceback (most recent call last)"
    idx = output.rfind(marker)
    if idx == -1:
        return output[-3000:] if len(output) > 3000 else output
    head = output[:idx].rstrip()
    tail = output[idx:]
    if len(head) > _ERROR_HEAD_CHARS:
        head = head[:_ERROR_HEAD_CHARS] + "\n…(truncated head)"
    if not head:
        return tail[-3000:]
    return f"{head}\n\n--- traceback ---\n{tail}"
