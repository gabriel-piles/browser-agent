"""The flow verify agent run: legacy verification pipeline + flow decision.

Mirrors :class:`VerifyDownloadsUseCase` exactly (same tools, retry
loop, splicing, missing-count recompute) with two differences:
the system prompt carries the flow decision addendum, and the
structured output is :class:`FlowVerificationReport` — the legacy
report plus the required ``decision``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from websockets.exceptions import ConnectionClosedError as _BrowserConnectionClosed

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.flow_verification_report import FlowVerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.check_pdf_tool import check_pdf
from browser_agent.use_cases.declare_paths_tool import declare_paths
from browser_agent.use_cases.query_db_tool import query_db
from browser_agent.use_cases.run_read_script_tool import run_read_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
from browser_agent.use_cases.verification_explore_tool import explore_page
from browser_agent.use_cases.verification_system_prompt import VERIFICATION_SYSTEM_PROMPT
from browser_agent.use_cases.flow_verify_decision_addendum import FLOW_VERIFY_DECISION_ADDENDUM

_AGENT_RUN_RETRIES = 2
_AGENT_RUN_RETRY_DELAY_S = 5.0
_BROWSER_RESTARTS = 1


class FlowVerifyDownloadsUseCase:
    """Build the flow verification agent, run it once, return the report."""

    def __init__(self, deps: VerificationAgentDeps, model) -> None:
        self._deps = deps
        self._model = model

    def _build_agent(self) -> Agent[VerificationAgentDeps, FlowVerificationReport]:
        return Agent(
            model=self._model,
            system_prompt=f"{VERIFICATION_SYSTEM_PROMPT}\n\n{FLOW_VERIFY_DECISION_ADDENDUM}",
            deps_type=VerificationAgentDeps,
            output_type=FlowVerificationReport,
            tools=[declare_paths, Tool(explore_page, sequential=True), check_pdf, query_db, run_read_script],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
        )

    async def execute(self, request: VerificationRequest) -> FlowVerificationReport:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent()
            run = await self._run_agent(agent, request.render_prompt())
            report = self._coerce_result(run)
            report = self._splice_tool_results(report)
            report = self._recompute_missing_count(report)
            self._log_usage(run)
            return report
        finally:
            await self._deps.browser_session.close()

    def _splice_tool_results(self, report: FlowVerificationReport) -> FlowVerificationReport:
        """Merge the real PdfCheckResult objects the tools accumulated."""
        if not self._deps.pdf_results:
            return report
        seen = {r.url for r in report.pdf_results}
        merged = list(report.pdf_results)
        for result in self._deps.pdf_results:
            if result.url not in seen:
                merged.append(result)
        return report.model_copy(update={"pdf_results": merged})

    @staticmethod
    def _recompute_missing_count(report: FlowVerificationReport) -> FlowVerificationReport:
        """Override the LLM-authored missing_count with the true non-present count."""
        missing = sum(1 for r in report.pdf_results if r.verdict != "present")
        return report.model_copy(update={"missing_count": missing})

    async def _run_agent(self, agent: Any, prompt: str) -> Any:
        agent_logger.bind(agent="flow_verifier").info(
            "START  prompt_tokens={n} prompt_preview={preview}",
            n=len(prompt) // 4,
            preview=_truncate(prompt, 200),
        )
        started = time.monotonic()
        try:
            run = await _run_with_retry(
                agent,
                prompt,
                deps=self._deps,
                usage_limits=UsageLimits(
                    request_limit=MAX_LLM_CALLS,
                    input_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
                ),
                agent_name="flow_verifier",
            )
        finally:
            agent_logger.bind(agent="flow_verifier").info(
                "END    elapsed={elapsed:.1f}s",
                elapsed=time.monotonic() - started,
            )
        return run

    @staticmethod
    def _log_usage(run: Any) -> None:
        usage = run.usage
        agent_logger.info(
            "USAGE  requests={req} input={input_tok} output={output_tok}",
            req=usage.requests,
            input_tok=usage.input_tokens,
            output_tok=usage.output_tokens,
        )

    @staticmethod
    def _coerce_result(run: Any) -> FlowVerificationReport:
        output = getattr(run, "output", None)
        if isinstance(output, FlowVerificationReport):
            return output
        raise RuntimeError(
            f"Agent returned an unsupported output type: {type(output).__name__}",
        )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"


async def _run_with_retry(
    agent: Agent,
    prompt: str,
    agent_name: str = "flow_verifier",
    **kwargs: Any,
) -> Any:
    """Run the agent, retrying transient errors up to twice (legacy copy)."""
    last_exc: Exception | None = None
    restarts = 0
    for attempt in range(_AGENT_RUN_RETRIES + 1):
        try:
            from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery

            return await run_agent_with_recovery(
                agent,
                prompt,
                deps=kwargs.get("deps"),
                usage_limits=kwargs.get("usage_limits"),
                message_history=kwargs.get("message_history"),
                agent_name=agent_name,
            )
        except _BrowserConnectionClosed as exc:
            last_exc = exc
            deps = kwargs.get("deps")
            browser = getattr(deps, "browser_session", None) if deps is not None else None
            if browser is None or restarts >= _BROWSER_RESTARTS:
                raise
            restarts += 1
            agent_logger.warning(
                "browser CDP connection lost (restart {n}/{max}); recycling browser session: {exc}",
                n=restarts,
                max=_BROWSER_RESTARTS,
                exc=exc,
            )
            try:
                await browser.close()
            except Exception as close_exc:  # noqa: BLE001 — recovery best-effort
                agent_logger.warning("browser close during recovery failed: {exc}", exc=close_exc)
            await browser.start()
            continue
        except Exception as exc:  # noqa: BLE001 — transient model errors
            last_exc = exc
            if attempt < _AGENT_RUN_RETRIES:
                agent_logger.warning(
                    "agent.run failed (attempt {n}/{max}), retrying in {d}s: {exc}",
                    n=attempt + 1,
                    max=_AGENT_RUN_RETRIES + 1,
                    d=_AGENT_RUN_RETRY_DELAY_S,
                    exc=exc,
                )
                await asyncio.sleep(_AGENT_RUN_RETRY_DELAY_S)
            else:
                raise
    raise RuntimeError("unreachable") if last_exc is None else last_exc
