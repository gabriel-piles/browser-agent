"""The link-discovery-verification use case: generate a script that checks discovery completeness.

Mirrors :class:`GenerateZendriverScriptUseCase` but with a different
system prompt and the :class:`LinkDiscoveryVerificationScript` output
type. Reuses the same ``explore_page`` and ``run_validation_script``
tools and the same :class:`AgentDeps` so the agent can drive the page
and test its verification script before emitting it.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.domain.link_discovery_verification_script import (
    LinkDiscoveryVerificationScript,
)
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.link_discovery_verification_system_prompt import (
    LINK_DISCOVERY_VERIFICATION_SYSTEM_PROMPT,
)
from browser_agent.use_cases.run_validation_script_tool import run_validation_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class GenerateLinkDiscoveryVerificationUseCase:
    """Build the agent, run it once, return the :class:`LinkDiscoveryVerificationScript`."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._last_messages: list = []

    def _build_agent(self, model: Model) -> Agent[AgentDeps, LinkDiscoveryVerificationScript]:
        agent: Agent[AgentDeps, LinkDiscoveryVerificationScript] = Agent(
            model=model,
            system_prompt=LINK_DISCOVERY_VERIFICATION_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=LinkDiscoveryVerificationScript,
            tools=[explore_page, run_validation_script],
            capabilities=[ToolReturnCompactor()],
        )
        return agent

    async def execute(self, prompt: str) -> LinkDiscoveryVerificationScript:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent(self._deps.llm.get_model())
            run = await self._run_agent(agent, prompt)
            self._last_messages = list(run.all_messages())
            return self._coerce_result(run)
        except Exception:
            await self._deps.browser_session.close()
            raise

    async def repair(self, feedback: str) -> LinkDiscoveryVerificationScript:
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, feedback, message_history=self._last_messages)
        self._last_messages = list(run.all_messages())
        return self._coerce_result(run)

    async def close(self) -> None:
        """Close the browser session after the run + any repairs are done."""
        await self._deps.browser_session.close()

    async def _run_agent(
        self,
        agent: Agent[AgentDeps, LinkDiscoveryVerificationScript],
        prompt: str,
        message_history: list | None = None,
    ) -> Any:
        agent_logger.info(
            "START link-discovery-verification prompt_tokens={n}",
            n=len(prompt) // 4,
        )
        started = time.monotonic()
        try:
            run = await agent.run(
                prompt,
                deps=self._deps,
                usage_limits=UsageLimits(request_limit=MAX_LLM_CALLS),
                message_history=message_history,
            )
        finally:
            agent_logger.info(
                "END link-discovery-verification elapsed={e:.1f}s",
                e=time.monotonic() - started,
            )
        self._log_usage(run)
        return run

    @staticmethod
    def _log_usage(run: Any) -> None:
        usage = run.usage
        agent_logger.info(
            "USAGE link-discovery-verification requests={r} input={i} output={o}",
            r=usage.requests,
            i=usage.input_tokens,
            o=usage.output_tokens,
        )

    @staticmethod
    def _coerce_result(run: Any) -> LinkDiscoveryVerificationScript:
        output = getattr(run, "output", None)
        if isinstance(output, LinkDiscoveryVerificationScript):
            return output
        raise RuntimeError(
            f"Agent returned an unsupported output type: {type(output).__name__}",
        )
