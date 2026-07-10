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

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.script_runner_port import ScriptRunnerPort
from browser_agent.use_cases.agent_deps import AgentDeps

VALIDATION_TIMEOUT_S = 90.0


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
    return _format_result(result, deps.validation_attempts, deps.validation_limit)


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


def _extract_error(output: str) -> str:
    """Return the last traceback block plus the final error line.

    Validation output is often dozens of lines of zendriver/CDP noise
    before the actual Python traceback. Feeding the whole thing to the
    LLM wastes context and buries the fixable error. We keep only the
    last ``Traceback (most recent call last)`` block (inclusive) and
    fall back to the tail of the output if no traceback is present.
    """
    marker = "Traceback (most recent call last)"
    idx = output.rfind(marker)
    if idx == -1:
        return output[-3000:] if len(output) > 3000 else output
    return output[idx:]
