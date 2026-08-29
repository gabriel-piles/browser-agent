"""LLM-only agent that adapts a proven sibling script to a target subtask."""

from __future__ import annotations

from pydantic_ai import Agent, UsageLimits

from loguru import logger

from browser_agent.agent_logging import record_llm_usage
from browser_agent.configuration import AGENT_INPUT_TOKEN_LIMIT, MAX_OUTPUT_TOKENS, ORCHESTRATOR_MAX_LLM_CALLS
from browser_agent.domain.script_reuse_decision import ScriptReuseDecision
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.use_cases.script_reuse_prompt import ScriptReusePrompt


_SYSTEM_PROMPT = """\
You adapt proven scraping scripts for sibling subtasks on the same site. \
Change only constants (labels, URLs, ranges) and keep all structure, \
selectors, and mechanics identical to the source. If the source's page \
type cannot serve the target subtask, reply with status "incompatible" \
rather than inventing new mechanics. Your reply is a single JSON object \
matching the ScriptReuseDecision schema.""".strip()


class ScriptReuseAdapter:
    """One constrained LLM call: adapt the source script or reject it."""

    def __init__(self) -> None:
        from browser_agent.adapters.llm.llm_adapter_factory import build_llm

        self._model = build_llm().get_model()

    def _build_agent(self) -> Agent[None, ScriptReuseDecision]:
        return Agent(
            model=self._model,
            system_prompt=_SYSTEM_PROMPT,
            output_type=ScriptReuseDecision,
            model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
            retries={"output": 3},
        )

    async def adapt(self, subtask: SubtaskSpec, source_code: str) -> ScriptReuseDecision:
        agent = self._build_agent()
        prompt = ScriptReusePrompt.render(subtask, source_code)
        run = await agent.run(prompt, usage_limits=_usage_limits())
        u = run.usage
        record_llm_usage("script_reuse", u.input_tokens or 0, u.output_tokens or 0, u.requests or 0)
        try:
            from browser_agent.llm_transcript_logger import write_llm_transcript

            write_llm_transcript(
                "script_reuse",
                prompt,
                list(run.all_messages()),
                {
                    "input_tokens": u.input_tokens or 0,
                    "output_tokens": u.output_tokens or 0,
                    "requests": u.requests or 0,
                },
            )
        except Exception:
            logger.exception("failed to persist script_reuse transcript")
        output = getattr(run, "output", None)
        if isinstance(output, ScriptReuseDecision):
            return output
        raise RuntimeError(f"Script reuse returned unsupported type: {type(output).__name__}")


def _usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=ORCHESTRATOR_MAX_LLM_CALLS,
        total_tokens_limit=AGENT_INPUT_TOKEN_LIMIT,
    )
