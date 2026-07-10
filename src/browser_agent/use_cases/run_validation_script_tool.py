"""The ``run_validation_script`` tool bound to the Pydantic-AI agent.

The tool takes a self-contained Python script (the same shape as the
final deliverable), runs it in a subprocess via the injected
:class:`ScriptRunnerPort`, and returns the exit code + combined
stdout/stderr. The agent uses this to validate its selectors, scroll
loops and filter logic *before* producing the final script.
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

    Use this tool to TEST a minimal script that proves your strategy
    works — a script that navigates to the target URL, finds the key
    selectors, and prints what it discovers (element counts, text,
    hrefs). If the validation script fails, read the error output,
    fix your approach, and re-run the validation. Only emit the final
    :class:`GeneratedScript` once a validation script succeeds.

    The script must be self-contained (imports its own dependencies,
    uses zendriver, ``asyncio.run(main())``) — exactly like the final
    deliverable, but focused on proving the strategy, not on
    collecting all the data.
    """
    runner: ScriptRunnerPort = ctx.deps.script_runner
    async with traced_tool("run_validation_script"):
        result: ScriptExecutionResult = await runner.run(python_code, timeout=VALIDATION_TIMEOUT_S)
    return _format_result(result)


def _format_result(result: ScriptExecutionResult) -> str:
    status = "SUCCESS" if result.success else f"FAILED (exit_code={result.exit_code})"
    return f"# Validation result: {status}\n\n{result.short_output()}"
