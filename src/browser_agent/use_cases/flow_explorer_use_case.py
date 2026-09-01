"""Flow Explorer agent: turn one split's prompt into a FlowSubtaskSpec."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, EXPLORER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.flow_subtask_spec import FlowSubtaskSpec
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery
from browser_agent.use_cases.download_pdf_tool import download_pdf
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.flow_explorer_system_prompt import FLOW_EXPLORER_SYSTEM_PROMPT
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class FlowExplorerUseCase:
    """Explore one split's pages and emit its self-contained spec."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps: AgentDeps = deps

    def _build_agent(self, model) -> Agent[AgentDeps, FlowSubtaskSpec]:
        return Agent(
            model=model,
            system_prompt=FLOW_EXPLORER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=FlowSubtaskSpec,
            tools=[Tool(explore_page, max_retries=3, sequential=True), Tool(download_pdf, max_retries=3)],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def execute(self, split_prompt: str, context: str = "") -> FlowSubtaskSpec:
        """Explore and return the spec for one split folder.

        ``context`` carries the overall original task (``## ORIGINAL TASK``
        block) plus the prior split's spec+script when one exists, so the
        explorer records what must change to adapt it.
        """
        await self._deps.browser_session.start()
        agent = self._build_agent(self._deps.llm.get_model())
        prompt = split_prompt
        if context:
            prompt = f"{context}\n\n---\n\n{split_prompt}"
        run = await self._run_agent(agent, prompt)
        return self._coerce_result(run)

    async def close(self) -> None:
        await self._deps.browser_session.close()

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:  # noqa: ANN401 — mirrors legacy typing
        agent_logger.bind(agent="flow_explorer").info(
            "flow explorer running prompt_tokens={t}",
            t=len(prompt) // 4,
        )
        return await run_agent_with_recovery(
            agent,  # type: ignore[arg-type] — generic Agent variance, legacy pattern
            prompt,
            self._deps,
            usage_limits=_usage_limits(),
            agent_name="flow_explorer",
        )

    @staticmethod
    def _coerce_result(run: Any) -> FlowSubtaskSpec:  # noqa: ANN401 — mirrors legacy typing
        output = getattr(run, "output", None)
        if isinstance(output, FlowSubtaskSpec):
            return output
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=EXPLORER_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
