"""The discovery-verification use case: an independent completeness auditor.

Mirrors :class:`VerifyDownloadsUseCase` structure: builds a Pydantic-AI
``Agent`` with the navigation/DB tools bound (deliberately NO
``check_pdf`` — nothing is downloaded at discovery time), the structured
:class:`VerificationReport` as result type, and the discovery system
prompt. The agent re-walks every manifest target on the live site and
compares per-target counts against ``discovered_links``.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.models import Model

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.discovery_verification_request import DiscoveryVerificationRequest
from browser_agent.domain.verification_report import VerificationReport
from browser_agent.use_cases.declare_paths_tool import declare_paths
from browser_agent.use_cases.query_db_tool import query_db
from browser_agent.use_cases.run_read_script_tool import run_read_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor
from browser_agent.use_cases.verification_agent_deps import VerificationAgentDeps
from browser_agent.use_cases.verification_explore_tool import explore_page
from browser_agent.use_cases.verification_system_prompt import DISCOVERY_VERIFICATION_SYSTEM_PROMPT
from browser_agent.use_cases.verify_downloads_use_case import _run_with_retry, _truncate

_AGENT_NAME = "discovery_verifier"


class VerifyDiscoveryUseCase:
    """Build the discovery-completeness agent, run it once, return the report."""

    def __init__(self, deps: VerificationAgentDeps, model: Model) -> None:
        self._deps = deps
        self._model = model

    def _build_agent(self) -> Agent[VerificationAgentDeps, VerificationReport]:
        agent: Agent[VerificationAgentDeps, VerificationReport] = Agent(
            model=self._model,
            system_prompt=DISCOVERY_VERIFICATION_SYSTEM_PROMPT,
            deps_type=VerificationAgentDeps,
            output_type=VerificationReport,
            tools=[declare_paths, Tool(explore_page, sequential=True), query_db, run_read_script],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
        )
        return agent

    async def execute(self, request: DiscoveryVerificationRequest) -> VerificationReport:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent()
            run = await self._run_agent(agent, request.render_prompt())
            report = self._coerce_result(run)
            self._log_usage(run)
            return report
        finally:
            await self._deps.browser_session.close()

    async def _run_agent(self, agent: Agent[VerificationAgentDeps, VerificationReport], prompt: str) -> Any:
        agent_logger.bind(agent=_AGENT_NAME).info(
            "START  prompt_tokens={n} prompt_preview={preview}",
            n=len(prompt) // 4,
            preview=_truncate(prompt, 200),
        )
        started = time.monotonic()
        try:
            run = await _run_with_retry(
                agent,
                prompt,
                agent_name=_AGENT_NAME,
                deps=self._deps,
                usage_limits=UsageLimits(
                    request_limit=MAX_LLM_CALLS,
                    input_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
                ),
            )
        finally:
            agent_logger.bind(agent=_AGENT_NAME).info(
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
