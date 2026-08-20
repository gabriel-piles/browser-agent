"""Script Builder agent: explore page to verify selectors, then write the script."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent, Tool, UsageLimits
from browser_agent.agent_logging import agent_logger
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, WRITER_MAX_LLM_CALLS, MAX_OUTPUT_TOKENS
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.agent_run_with_overflow_recovery import run_agent_with_recovery
from browser_agent.use_cases.builder_system_prompt import BUILDER_SYSTEM_PROMPT
from browser_agent.use_cases.explore_page_tool import explore_page
from browser_agent.use_cases.run_validation_script_tool import run_validation_script
from browser_agent.use_cases.tool_return_compactor import ToolReturnCompactor


class ScriptBuilderUseCase:
    """Write, validate, and emit a script for one subtask."""

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps
        self._last_messages: list = []

    def _build_agent(self, model) -> Agent[AgentDeps, GeneratedScript]:
        return Agent(
            model=model,
            system_prompt=BUILDER_SYSTEM_PROMPT,
            deps_type=AgentDeps,
            output_type=GeneratedScript,
            tools=[
                Tool(explore_page, max_retries=3),
                Tool(run_validation_script, max_retries=3),
            ],
            capabilities=[ToolReturnCompactor()],
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def execute(self, subtask: SubtaskSpec, context: str = "") -> GeneratedScript:
        prompt = self._build_prompt(subtask, context)
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, prompt)
        self._last_messages = list(run.all_messages())
        script = self._coerce_result(run)
        if script.kind != subtask.kind:
            script = script.model_copy(update={"kind": subtask.kind})
        return script

    async def repair(self, feedback: str) -> GeneratedScript:
        agent = self._build_agent(self._deps.llm.get_model())
        run = await self._run_agent(agent, feedback, message_history=self._last_messages)
        self._last_messages = list(run.all_messages())
        return self._coerce_result(run)

    @staticmethod
    def _build_prompt(subtask: SubtaskSpec, context: str) -> str:
        parts: list[str] = []
        if context:
            parts.append(context)
        parts.append(f"## Subtask: {subtask.subtask_id}")
        parts.append(f"**Kind**: {subtask.kind}")
        parts.append(f"**Description**: {subtask.description}")
        if subtask.verified_selectors:
            parts.append("**Verified Selectors**:")
            parts.extend(f"- {s}" for s in subtask.verified_selectors)
        if subtask.field_specs:
            parts.append("**Field Specs** (paste verbatim as FIELD_SPECS):")
            parts.append("```json")
            parts.append(json.dumps([fs.model_dump(mode="json") for fs in subtask.field_specs], indent=2))
            parts.append("```")
        if subtask.sample_document_urls:
            parts.append("**Sample Document URLs**:")
            parts.extend(f"- {u}" for u in subtask.sample_document_urls[:5])
        parts.append(f"**PDF Download Strategy**: {subtask.pdf_download_strategy}")
        parts.append(
            "**Idempotency reminder**: skip downloads whose target file already exists; "
            "only process discovered_links rows with status='discovered'; "
            "save_record upserts by source_url — never error on already-existing data."
        )
        return "\n".join(parts)

    async def _run_agent(self, agent: Agent, prompt: str, message_history: list | None = None) -> Any:
        agent_logger.info(
            "script builder running prompt_tokens={t} messages={m}",
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
    def _coerce_result(run: Any) -> GeneratedScript:
        output = getattr(run, "output", None)
        if isinstance(output, GeneratedScript):
            return output
        raise RuntimeError(f"Agent returned an unsupported output type: {type(output).__name__}")


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=WRITER_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
