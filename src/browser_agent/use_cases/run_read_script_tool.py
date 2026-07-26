"""The ``run_read_script`` tool bound to the verification agent.

The agent writes self-contained read-only Python to cross-reference
``metadata.db`` against ``downloads/`` and inspect file integrity.
``DB_PATH`` and ``DOWNLOADS_PATH`` are pre-injected so the script does
not need to guess the run layout. The script runs in an isolated
subprocess with no handle to the agent's browser session — network and
downloads are forbidden by the system prompt and by isolation.

A hard counter on :class:`VerificationAgentDeps` caps how many runs one
agent turn may perform, mirroring the ``check_pdf`` limit backstop.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps

_RUN_TIMEOUT_S = 60.0


async def run_read_script(ctx: RunContext[VerificationAgentDeps], python_code: str) -> str:
    """Run read-only ``python_code`` and return the captured result.

    Use this to cross-reference the DB against the downloads folder and
    inspect file integrity — list files with sizes, parse ``%PDF`` magic
    bytes, compute coverage stats. ``DB_PATH`` and ``DOWNLOADS_PATH`` are
    pre-injected constants. Do NOT import zendriver / curl_cffi / httpx
    / aiohttp / requests / urllib or perform any network or download —
    this tool is READ-ONLY with respect to the run's PDFs.

    You have a HARD limit of ``script_run_limit`` runs per turn. When
    the limit is reached the tool refuses to run and tells you to emit
    the report.
    """
    deps = ctx.deps
    if deps.script_runs >= deps.script_run_limit:
        return _limit_reached(deps)
    deps.script_runs += 1
    code = _inject_constants(deps, python_code)
    async with traced_tool("run_read_script", summary=python_code[:120]):
        result: ScriptExecutionResult = await deps.script_runner.run(code, timeout=_RUN_TIMEOUT_S)
    return _format_result(result, deps.script_runs, deps.script_run_limit)


def _inject_constants(deps: VerificationAgentDeps, python_code: str) -> str:
    """Prepend ``DB_PATH`` / ``DOWNLOADS_PATH`` constants before the agent's code."""
    header = (
        "from pathlib import Path\n"
        f"DB_PATH = Path({str(deps.db_path)!r})\n"
        f"DOWNLOADS_PATH = Path({str(deps.downloads_path)!r})\n"
        "\n"
    )
    return header + python_code


def _limit_reached(deps: VerificationAgentDeps) -> str:
    return (
        f"# run_read_script limit reached ({deps.script_run_limit}/{deps.script_run_limit}).\n"
        "You have used all your script runs. STOP calling this tool.\n"
        "Emit the final VerificationReport now using the evidence you have gathered.\n"
        "Do not call run_read_script again."
    )


def _format_result(result: ScriptExecutionResult, attempt: int, limit: int) -> str:
    """Format the result like ``run_validation_script_tool._format_result``."""
    status = "SUCCESS" if result.success else f"FAILED (exit_code={result.exit_code})"
    header = f"# Script run {attempt}/{limit}: {status}"
    body = result.output if result.success else _extract_error(result.output)
    remaining = limit - attempt
    footer = (
        f"\n# You have {remaining} script run(s) remaining."
        if remaining > 0
        else "\n# This was your LAST script run. Emit the report now."
    )
    return f"{header}\n\n{body}{footer}"


def _extract_error(output: str) -> str:
    """Return the last traceback block plus the final error line."""
    marker = "Traceback (most recent call last)"
    idx = output.rfind(marker)
    if idx == -1:
        return output[-3000:] if len(output) > 3000 else output
    return output[idx:]
