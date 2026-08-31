"""Task Discover agent: explore the site and produce a DiscoverPlan."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, DISCOVER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.discover_plan import DiscoverPlan
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery
from browser_agent.use_cases.discover_system_prompt import DISCOVER_SYSTEM_PROMPT
from browser_agent.use_cases.download_pdf_tool import download_pdf
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class TaskDiscoverUseCase:
    """Explore the site and split the task into WHAT-scoped chunks. No code writing."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps

    def _build_agent(self, model) -> Agent[AgentDeps, DiscoverPlan]:
        return Agent(
            model=model,
            system_prompt=DISCOVER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=DiscoverPlan,
            tools=[Tool(explore_page, max_retries=3, sequential=True), Tool(download_pdf, max_retries=3)],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def execute(self, task: str, context: str = "") -> DiscoverPlan:
        await self._deps.browser_session.start()
        agent = self._build_agent(self._deps.llm.get_model())
        prompt = task
        if context:
            prompt = f"{context}\n\n---\n\n{task}"
        run = await self._run_agent(agent, prompt)
        plan = self._coerce_result(run)
        self._log_plan(plan)
        return plan

    async def close(self) -> None:
        await self._deps.browser_session.close()

    async def _run_agent(self, agent: Any, prompt: str) -> Any:
        agent_logger.bind(agent="task_discover").info(
            "task discoverer running prompt_tokens={t}",
            t=len(prompt) // 4,
        )
        return await run_agent_with_recovery(
            agent,
            prompt,
            self._deps,
            usage_limits=_usage_limits(),
            agent_name="task_discover",
        )

    @staticmethod
    def _coerce_result(run: Any) -> DiscoverPlan:
        output = getattr(run, "output", None)
        if isinstance(output, DiscoverPlan):
            return output
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")

    @staticmethod
    def _log_plan(plan: DiscoverPlan) -> None:
        agent_logger.info(
            "task discoverer produced plan: {n} splits",
            n=len(plan.splits),
        )


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=DISCOVER_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
