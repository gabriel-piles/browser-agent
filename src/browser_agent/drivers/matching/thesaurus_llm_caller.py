"""Call the LLM for one thesaurus and parse its YAML output.

Hides the prompt construction, the per-call value truncation,
the pydantic-ai agent invocation, and the YAML-output parsing
behind one object. The match driver calls :meth:`call` once
per thesaurus and gets back the list of
:class:`ThesaurusMappingEntry` objects the LLM produced (or a
``[]`` short-circuit when nothing remains for the LLM).
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.thesaurus_mapping_entry import ThesaurusMappingEntry
from browser_agent.drivers.matching.thesaurus_llm_output_parser import ThesaurusLlmOutputParser
from browser_agent.drivers.matching.thesaurus_match_prompt_builder import (
    ThesaurusMatchPromptBuilder,
)
from browser_agent.ports.llm_port import LlmPort
from pydantic_ai import Agent, UsageLimits

# Cap how many values ship in the LLM prompt for one thesaurus.
MAX_VALUES_PER_LLM_CALL = 50


class ThesaurusLlmCaller:
    """Call the LLM for one thesaurus and parse its YAML output into entries."""

    def __init__(
        self,
        prompt_builder: ThesaurusMatchPromptBuilder,
        output_parser: ThesaurusLlmOutputParser,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._output_parser = output_parser

    async def call(
        self,
        llm: LlmPort,
        thesaurus: ThesauriSnapshot,
        thesaurus_values: tuple[str, ...],
        remaining_map: dict[str, int],
    ) -> list[ThesaurusMappingEntry]:
        """Call the LLM and return the parsed :class:`ThesaurusMappingEntry` list."""
        if not remaining_map:
            return []
        prompt, system_prompt, truncated_map = self._build_call_inputs(thesaurus, thesaurus_values, remaining_map)
        self._log_call(truncated_map)
        llm_text = await self._run_match_agent(llm, prompt, system_prompt)
        return self._output_parser.parse(llm_text, truncated_map, thesaurus_values)

    def _build_call_inputs(
        self,
        thesaurus: ThesauriSnapshot,
        thesaurus_values: tuple[str, ...],
        remaining_map: dict[str, int],
    ) -> tuple[str, str, dict[str, int]]:
        """Return the ``(user_prompt, system_prompt, truncated_map)`` triple."""
        truncated = list(remaining_map.items())[:MAX_VALUES_PER_LLM_CALL]
        prompt = self._prompt_builder.build(thesaurus.name, thesaurus_values, truncated)
        system_prompt = self._prompt_builder.system_prompt()
        return prompt, system_prompt, dict(truncated)

    def _log_call(self, truncated_map: dict[str, int]) -> None:
        """Log the size of the truncated LLM-bound value set."""
        print(f"  Calling LLM for {len(truncated_map)} unmatched value(s)...")

    async def _run_match_agent(
        self,
        llm: LlmPort,
        prompt: str,
        system_prompt: str,
    ) -> str:
        """Run the pydantic-ai agent for one thesaurus call and return its output text."""
        agent = Agent(
            model=llm.get_model(),
            system_prompt=system_prompt,
            output_type=str,
        )
        limits = UsageLimits(request_limit=MAX_LLM_CALLS)
        run = await agent.run(prompt, usage_limits=limits)
        return run.output
