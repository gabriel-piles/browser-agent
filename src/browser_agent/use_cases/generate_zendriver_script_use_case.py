"""The single end-to-end use case: turn a user task into a Zendriver script.

The use case is a thin object — it builds a Pydantic-AI ``Agent`` with
the inspection tool bound, the structured ``GeneratedScript`` as the
result type, and the system prompt that encodes the script rules. It
then runs the agent and packages the output back as a
:class:`GeneratedScript` for the caller.

No retry, no streaming, no logging fan-out. Pydantic-AI handles the
agent loop; we just hand it the deps and the prompt and trust the
structured-output validation to surface malformed model output as a
clear error.
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
from browser_agent.use_cases.inspect_html_tool import inspect_html
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
        agent.tool(inspect_html)
        agent.tool(run_validation_script)
        return agent

    async def execute(self, request: CodeGenerationRequest) -> GeneratedScript:
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, request.render_prompt())
        script = self._coerce_result(run)
        self._log_script(script)
        return script

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
            "SCRIPT deps={deps} lines={lines} has_async_main={ok}",
            deps=script.dependency_names(),
            lines=script.line_count(),
            ok=script.has_async_main(),
        )

    @staticmethod
    def _log_usage(run: Any) -> None:
        usage = run.usage
        agent_logger.info(
            "USAGE  in_tokens={in_t} out_tokens={out_t} requests={req}",
            in_t=usage.input_tokens,
            out_t=usage.output_tokens,
            req=usage.requests,
        )

    @staticmethod
    def _coerce_result(run: Any) -> GeneratedScript:
        output = getattr(run, "output", None)
        if isinstance(output, GeneratedScript):
            return output
        if isinstance(output, dict):
            return GeneratedScript.model_validate(output)
        if isinstance(output, str):
            return GeneratedScript.model_validate_json(output)
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(total={len(value) // 4} tokens)"


def run_sync(request: CodeGenerationRequest, deps: AgentDeps) -> GeneratedScript:
    """Convenience helper for callers that want a synchronous entry point."""
    return asyncio.run(GenerateZendriverScriptUseCase(deps).execute(request))
