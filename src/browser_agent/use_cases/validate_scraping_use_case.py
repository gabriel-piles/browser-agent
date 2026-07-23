"""The validation use case: run an independent agent to check scraping coverage.

Mirrors :class:`GenerateZendriverScriptUseCase` structure: builds a
Pydantic-AI ``Agent`` with the ``explore_page`` and ``check_pdf`` tools
bound, the structured ``ValidationReport`` as the result type, and the
validation system prompt. Runs the agent and packages the output back
as a :class:`ValidationReport` for the caller.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model
from browser_agent.agent_logging import agent_logger

from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.domain.validation_report import ValidationReport
from browser_agent.domain.validation_request import ValidationRequest
from browser_agent.use_cases.check_pdf_tool import check_pdf
from browser_agent.use_cases.validation_agent_deps import ValidationAgentDeps
from browser_agent.use_cases.validation_explore_tool import explore_page
from browser_agent.use_cases.validation_system_prompt import VALIDATION_SYSTEM_PROMPT


class ValidateScrapingUseCase:
    """Build the validation agent, run it once, return the report."""

    def __init__(self, deps: ValidationAgentDeps, model: Model) -> None:
        self._deps = deps
        self._model = model

    def _build_agent(self) -> Agent[ValidationAgentDeps, ValidationReport]:
        agent: Agent[ValidationAgentDeps, ValidationReport] = Agent(
            model=self._model,
            system_prompt=VALIDATION_SYSTEM_PROMPT,
            deps_type=ValidationAgentDeps,
            output_type=ValidationReport,
            tools=[explore_page, check_pdf],
        )
        return agent

    async def execute(self, request: ValidationRequest) -> ValidationReport:
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
    def _coerce_result(run: Any) -> ValidationReport:
        output = getattr(run, "output", None)
        if isinstance(output, ValidationReport):
            return output
        raise RuntimeError(
            f"Agent returned an unsupported output type: {type(output).__name__}",
        )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"
