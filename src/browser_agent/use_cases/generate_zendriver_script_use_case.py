"""The single end-to-end use case: turn a user task into a Zendriver script.

The use case is a thin object — it builds a Pydantic-AI ``Agent`` with
the exploration and validation tools bound, the structured
``GeneratedScript`` as the result type, and the system prompt that
encodes the script rules. It then runs the agent and packages the
output back as a :class:`GeneratedScript` for the caller.

The browser session (a :class:`BrowserSessionPort`) is started before
the agent runs and torn down after it finishes, so one Chrome instance
serves all ``explore_page`` calls for the entire run. No retry, no
streaming, no logging fan-out. Pydantic-AI handles the agent loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model

from browser_agent.agent_logging import agent_logger
from browser_agent.domain.code_generation_request import CodeGenerationRequest
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.run_validation_script_tool import run_validation_script
from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.use_cases.system_prompt import SYSTEM_PROMPT


class GenerateZendriverScriptUseCase:
    """Build the agent, run it once, return the :class:`GeneratedScript`."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps

    def _build_agent(self, model: Model) -> Agent[AgentDeps, GeneratedScript]:
        agent: Agent[AgentDeps, GeneratedScript] = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=GeneratedScript,
        )
        agent.tool(explore_page)
        agent.tool(run_validation_script)
        return agent

    async def execute(self, request: CodeGenerationRequest) -> GeneratedScript:
        await self._deps.browser_session.start()
        try:
            agent = self._build_agent(self._deps.llm.get_model())
            run = await self._run_agent(agent, request.render_prompt())
            script = self._coerce_result(run)
            self._log_script(script)
            return script
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
            run = await agent.run(prompt, deps=self._deps, usage_limits=UsageLimits(request_limit=MAX_LLM_CALLS))
        finally:
            agent_logger.info(
                "END    elapsed={elapsed:.1f}s",
                elapsed=time.monotonic() - started,
            )
        self._log_usage(run)
        return run

    @staticmethod
    def _log_script(script: GeneratedScript) -> None:
        agent_logger.info(
            "SCRIPT  lines={lines} deps={deps} preview={preview}",
            lines=script.line_count(),
            deps=script.dependency_names(),
            preview=_truncate(script.python_code, 200),
        )

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
    def _coerce_result(run: Any) -> GeneratedScript:
        output = getattr(run, "output", None)
        if isinstance(output, GeneratedScript):
            return output
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"


def run_sync(request: CodeGenerationRequest, deps: AgentDeps) -> GeneratedScript:
    """Convenience helper for callers that want a synchronous entry point."""
    return asyncio.run(GenerateZendriverScriptUseCase(deps).execute(request))
