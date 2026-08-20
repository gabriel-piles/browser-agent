"""The download-verification use case: run an independent agent to verify coverage.

Mirrors :class:`GenerateZendriverScriptUseCase` structure: builds a
Pydantic-AI ``Agent`` with the four verification tools bound, the
structured ``VerificationReport`` as the result type, and the
verification system prompt. Runs the agent and packages the output
back as a :class:`VerificationReport` for the caller.
"""

from __future__ import annotations

import time
import asyncio
from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.models import Model
from websockets.exceptions import ConnectionClosedError as _BrowserConnectionClosed

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery
from browser_agent.use_cases.check_pdf_tool import check_pdf
from browser_agent.use_cases.declare_paths_tool import declare_paths
from browser_agent.use_cases.query_db_tool import query_db
from browser_agent.use_cases.run_read_script_tool import run_read_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
from browser_agent.use_cases.verification_explore_tool import explore_page
from browser_agent.use_cases.verification_system_prompt import VERIFICATION_SYSTEM_PROMPT


class VerifyDownloadsUseCase:
    """Build the verification agent, run it once, return the report."""

    def __init__(self, deps: VerificationAgentDeps, model: Model) -> None:
        self._deps = deps
        self._model = model

    def _build_agent(self) -> Agent[VerificationAgentDeps, VerificationReport]:
        agent: Agent[VerificationAgentDeps, VerificationReport] = Agent(
            model=self._model,
            system_prompt=VERIFICATION_SYSTEM_PROMPT,
            deps_type=VerificationAgentDeps,
            output_type=VerificationReport,
            tools=[declare_paths, Tool(explore_page, sequential=True), check_pdf, query_db, run_read_script],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
        )
        return agent

    async def execute(self, request: VerificationRequest) -> VerificationReport:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent()
            run = await self._run_agent(agent, request.render_prompt())
            report = self._coerce_result(run)
            report = self._splice_tool_results(report)
            self._log_usage(run)
            return report
        finally:
            await self._deps.browser_session.close()

    def _splice_tool_results(self, report: VerificationReport) -> VerificationReport:
        """Merge the real PdfCheckResult objects the tools accumulated."""
        if not self._deps.pdf_results:
            return report
        seen = {r.url for r in report.pdf_results}
        merged = list(report.pdf_results)
        for result in self._deps.pdf_results:
            if result.url not in seen:
                merged.append(result)
        return report.model_copy(update={"pdf_results": merged})

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        agent_logger.info(
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
            )
        finally:
            agent_logger.info(
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
    def _coerce_result(run: Any) -> VerificationReport:
        output = getattr(run, "output", None)
        if isinstance(output, VerificationReport):
            return output
        raise RuntimeError(
            f"Agent returned an unsupported output type: {type(output).__name__}",
        )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"


_AGENT_RUN_RETRIES = 2
_AGENT_RUN_RETRY_DELAY_S = 5.0
_BROWSER_RESTARTS = 1


async def _run_with_retry(agent: Agent, prompt: str, **kwargs: Any) -> Any:
    """Run the agent, retrying transient model errors up to twice.

    Context overflow is handled inside
    :func:`run_agent_with_recovery`, which preserves partial history
    and retries once with a finalize directive; a second overflow
    propagates. Lost CDP connections recycle the browser session once
    and rerun. Transient errors keep the existing retry-with-delay
    behavior.
    """
    last_exc: Exception | None = None
    restarts = 0
    for attempt in range(_AGENT_RUN_RETRIES + 1):
        try:
            return await run_agent_with_recovery(
                agent,
                prompt,
                deps=kwargs.get("deps"),
                usage_limits=kwargs.get("usage_limits"),
                message_history=kwargs.get("message_history"),
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
