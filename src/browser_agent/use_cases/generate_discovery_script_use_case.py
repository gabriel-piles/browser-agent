"""Agent 2: write the discovery script that collects document links.

The Discovery Writer receives a focused prompt from the Explorer,
explores the page to verify link-collection mechanics if needed, writes
the discovery script, validates it via ``run_validation_script``, and
emits it. It is ALWAYS single-tab — no concurrency, no PDF downloads,
no metadata. The browser session is already started by the Explorer;
this use case does NOT start or close it.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimitExceeded

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, WRITER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.task_split import TaskSplit
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.discovery_writer_system_prompt import DISCOVERY_WRITER_SYSTEM_PROMPT
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.run_validation_script_tool import run_validation_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery


class GenerateDiscoveryScriptUseCase:
    """Write, validate, and emit the discovery script."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._last_messages: list = []

    def _build_agent(self, model: Model) -> Agent[AgentDeps, GeneratedScript]:
        return Agent(
            model=model,
            system_prompt=DISCOVERY_WRITER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=GeneratedScript,
            tools=[Tool(explore_page, max_retries=3), Tool(run_validation_script, max_retries=3)],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def execute(self, task_split: TaskSplit) -> GeneratedScript:
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, task_split.discovery_prompt)
        self._last_messages = list(run.all_messages())
        return self._coerce_result(run)

    async def repair(self, feedback: str) -> GeneratedScript:
        """Run a repair turn with the prior message history."""
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, feedback, message_history=self._last_messages)
        self._last_messages = list(run.all_messages())
        return self._coerce_result(run)

    async def _run_agent(self, agent: Agent, prompt: str, message_history: list | None = None) -> Any:
        agent_logger.info(
            "START  prompt_tokens={n} prompt_preview={preview}",
            n=len(prompt) // 4,
            preview=_truncate(prompt, 200),
        )
        started = time.monotonic()
        run = await self._run_agent_inner(agent, prompt, message_history)
        agent_logger.info("END    elapsed={elapsed:.1f}s", elapsed=time.monotonic() - started)
        self._log_usage(run)
        return run

    async def _run_agent_inner(self, agent: Agent, prompt: str, message_history: list | None) -> Any:
        try:
            return await run_agent_with_recovery(agent, prompt, self._deps, _usage_limits(), message_history)
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            agent_logger.warning("Context overflow after recovery retry: {exc}", exc=exc)
            raise

    @staticmethod
    def _coerce_result(run: Any) -> GeneratedScript:
        output = getattr(run, "output", None)
        if isinstance(output, GeneratedScript):
            return output.model_copy(update={"kind": "discovery"})
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")

    @staticmethod
    def _log_usage(run: Any) -> None:
        usage = run.usage
        agent_logger.info(
            "USAGE  requests={req} input={input_tok} output={output_tok}",
            req=usage.requests,
            input_tok=usage.input_tokens,
            output_tok=usage.output_tokens,
        )


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=WRITER_MAX_LLM_CALLS,
        input_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"
