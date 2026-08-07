"""Agent 1: explore the target site and produce a :class:`TaskSplit`.

The Explorer navigates the site via ``explore_page``, probes the PDF
download strategy via ``download_pdf``, and returns a structured
:class:`TaskSplit` — two focused natural-language prompts for the
Discovery Writer and the Processing Writer. It does NOT write code.
The browser session is started here and stays open for the writer
agents; :meth:`close` tears it down after all agents finish.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimitExceeded

from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import COMPACT_MAX_RETAINED, EXPLORER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.task_split import TaskSplit
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.download_pdf_tool import download_pdf
from browser_agent.use_cases.explorer_system_prompt import EXPLORER_SYSTEM_PROMPT
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class ExploreSiteUseCase:
    """Explore the site and decide the task split. No code writing."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._last_messages: list = []

    def _build_agent(self, model: Model) -> Agent[AgentDeps, TaskSplit]:
        return Agent(
            model=model,
            system_prompt=EXPLORER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=TaskSplit,
            tools=[explore_page, download_pdf],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
        )

    async def execute(self, request: CodeGenerationRequest) -> TaskSplit:
        await self._deps.browser_session.start()
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, request.render_prompt())
        self._last_messages = list(run.all_messages())
        split = self._coerce_result(run)
        self._log_split(split)
        return split

    async def close(self) -> None:
        """Close the browser session after all agents finish."""
        await self._deps.browser_session.close()

    async def _run_agent(self, agent: Agent, prompt: str) -> Any:
        agent_logger.info(
            "START  prompt_tokens={n} prompt_preview={preview}",
            n=len(prompt) // 4,
            preview=_truncate(prompt, 200),
        )
        started = time.monotonic()
        run = await self._run_agent_inner(agent, prompt, None)
        agent_logger.info("END    elapsed={elapsed:.1f}s", elapsed=time.monotonic() - started)
        self._log_usage(run)
        return run

    async def _run_agent_inner(self, agent: Agent, prompt: str, message_history: list | None) -> Any:
        try:
            return await agent.run(
                prompt,
                deps=self._deps,
                usage_limits=UsageLimits(request_limit=EXPLORER_MAX_LLM_CALLS),
                message_history=message_history,
            )
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            if message_history is not None:
                agent_logger.warning("Context overflow, retrying with truncated history: {exc}", exc=exc)
                truncated = list(message_history[-COMPACT_MAX_RETAINED:])
                return await agent.run(
                    prompt,
                    deps=self._deps,
                    usage_limits=UsageLimits(request_limit=EXPLORER_MAX_LLM_CALLS),
                    message_history=truncated,
                )
            agent_logger.warning("Context overflow on fresh run, retrying with finalize directive: {exc}", exc=exc)
            directive = "\n\nIMPORTANT: your exploration context is full. Emit your final structured result now without further tool calls."
            return await agent.run(
                prompt + directive,
                deps=self._deps,
                usage_limits=UsageLimits(request_limit=EXPLORER_MAX_LLM_CALLS),
            )

    @staticmethod
    def _coerce_result(run: Any) -> TaskSplit:
        output = getattr(run, "output", None)
        if isinstance(output, TaskSplit):
            return output
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

    @staticmethod
    def _log_split(split: TaskSplit) -> None:
        agent_logger.info(
            "SPLIT  needs_discovery={nd} samples={n} strategy={s}",
            nd=split.needs_discovery,
            n=len(split.sample_document_urls),
            s=split.pdf_download_strategy,
        )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"
