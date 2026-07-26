"""The download-verification use case: run an independent agent to verify coverage.

Mirrors :class:`GenerateZendriverScriptUseCase` structure: builds a
Pydantic-AI ``Agent`` with the four verification tools bound, the
structured ``VerificationReport`` as the result type, and the
verification system prompt. Runs the agent and packages the output
back as a :class:`VerificationReport` for the caller.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.domain.verification_request import VerificationRequest
from browser_agent.use_cases.check_pdf_tool import check_pdf
from browser_agent.use_cases.query_db_tool import query_db
from browser_agent.use_cases.run_read_script_tool import run_read_script
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
            tools=[explore_page, check_pdf, query_db, run_read_script],
        )
        return agent

    async def execute(self, request: VerificationRequest) -> VerificationReport:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent()
            run = await self._run_agent(agent, request.render_prompt())
            report = self._coerce_result(run)
            self._log_usage(run)
            return report
        finally:
            await self._deps.browser_session.close()

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        agent_logger.info(
            "START  prompt_tokens={n} prompt_preview={preview}",
            n=len(prompt) // 4,
            preview=_truncate(prompt, 200),
        )
        started = time.monotonic()
        try:
            run = await agent.run(
                prompt,
                deps=self._deps,
                usage_limits=UsageLimits(request_limit=MAX_LLM_CALLS),
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
