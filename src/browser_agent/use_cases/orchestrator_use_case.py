"""Orchestrator agent: LLM judgment point, no browser, no tools."""

from __future__ import annotations

from pydantic_ai import Agent, UsageLimits

from browser_agent.agent_logging import (
    agent_logger,
    record_llm_usage,
)
from browser_agent.configuration import (
    AGENT_INPUT_TOKEN_LIMIT,
    MAX_OUTPUT_TOKENS,
    ORCHESTRATOR_MAX_LLM_CALLS,
)
from browser_agent.domain.orchestrator_decision import OrchestratorDecision
from browser_agent.use_cases.orchestrator_system_prompt import ORCHESTRATOR_SYSTEM_PROMPT
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class OrchestratorUseCase:
    """LLM-only judgment agent — no AgentDeps, no tools, no browser."""

    def __init__(self) -> None:
        from browser_agent.adapters.llm.opencode_zen_adapter import OpenCodeZenAdapter

        self._model = OpenCodeZenAdapter().get_model()

    def _build_agent(self) -> Agent[None, OrchestratorDecision]:
        return Agent(
            model=self._model,
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            output_type=OrchestratorDecision,
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def decide(self, summary: str) -> OrchestratorDecision:
        agent = self._build_agent()
        agent_logger.bind(agent="orchestrator").info(
            "orchestrator deciding summary_tokens={t}",
            t=len(summary) // 4,
        )
        run = await agent.run(summary, usage_limits=_usage_limits())
        u = run.usage
        record_llm_usage("orchestrator", u.input_tokens or 0, u.output_tokens or 0, u.requests or 0)
        output = getattr(run, "output", None)
        if isinstance(output, OrchestratorDecision):
            return output
        raise RuntimeError(f"Orchestrator returned unsupported type: {type(output).__name__}")


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=ORCHESTRATOR_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
