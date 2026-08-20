"""Task Planner agent: explore the site and produce a ScrapePlan."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, EXPLORER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.scrape_plan import ScrapePlan
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery
from browser_agent.use_cases.download_pdf_tool import download_pdf
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.planner_system_prompt import PLANNER_SYSTEM_PROMPT
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class TaskPlannerUseCase:
    """Explore the site and produce a ScrapePlan. No code writing."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._last_messages: list = []

    def _build_agent(self, model) -> Agent[AgentDeps, ScrapePlan]:
        return Agent(
            model=model,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=ScrapePlan,
            tools=[Tool(explore_page, max_retries=3), Tool(download_pdf, max_retries=3)],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def execute(self, task: str, context: str = "") -> ScrapePlan:
        await self._deps.browser_session.start()
        agent = self._build_agent(self._deps.llm.get_model())
        prompt = task
        if context:
            prompt = f"{context}\n\n---\n\n{task}"
        run = await self._run_agent(agent, prompt)
        self._last_messages = list(run.all_messages())
        plan = self._coerce_result(run)
        self._log_plan(plan)
        return plan

    async def replan(self, focus: str) -> ScrapePlan:
        agent = self._build_agent(self._deps.llm.get_model())
        prompt = f"The previous plan needs revision. Focus: {focus}"
        run = await self._run_agent(agent, prompt, message_history=self._last_messages)
        self._last_messages = list(run.all_messages())
        plan = self._coerce_result(run)
        self._log_plan(plan)
        return plan

    async def close(self) -> None:
        await self._deps.browser_session.close()

    async def _run_agent(self, agent: Agent, prompt: str, message_history: list | None = None) -> Any:
        agent_logger.info(
            "task planner running prompt_tokens={t} messages={m}",
            t=len(prompt) // 4,
            m=len(message_history) if message_history else 0,
        )
        return await run_agent_with_recovery(
            agent,
            prompt,
            self._deps,
            usage_limits=_usage_limits(),
            message_history=message_history,
        )

    @staticmethod
    def _coerce_result(run: Any) -> ScrapePlan:
        output = getattr(run, "output", None)
        if isinstance(output, ScrapePlan):
            return output
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")

    @staticmethod
    def _log_plan(plan: ScrapePlan) -> None:
        agent_logger.info(
            "task planner produced plan: {n} subtasks",
            n=len(plan.subtasks),
        )


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=EXPLORER_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
