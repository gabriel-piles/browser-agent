"""LLM-driven use case: draft a Uwazi mapping from a field catalog.

Given a :class:`MetadataFieldCatalog` (built from the live
``metadata.db`` rows) and a Uwazi template name, the use case
sends a structured pydantic-ai ``Agent`` call to the LLM, gets
back an :class:`LlmMappingDraft`, validates it into a
:class:`UwaziMapping`, and (optionally) writes the YAML.

This is the only use case in the Uwazi sync flow that talks to the
LLM; the apply pipeline is pure. Prompt rendering, draft coercion
and post-LLM fallbacks each live in their own class
(:class:`ProposePromptRenderer`, :class:`LlmDraftAssembler`,
:class:`MappingFallbackFiller`); this class only wires them together
and runs the agent.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models import Model

from browser_agent.configuration import MAX_LLM_CALLS
from browser_agent.domain.llm_mapping_draft import LlmMappingDraft
from browser_agent.domain.metadata_field_catalog import MetadataFieldCatalog
from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from browser_agent.ports.llm_port import LlmPort
from browser_agent.use_cases.llm_draft_assembler import LlmDraftAssembler
from browser_agent.use_cases.mapping_fallback_filler import MappingFallbackFiller
from browser_agent.use_cases.propose_prompt_renderer import ProposePromptRenderer
from browser_agent.use_cases.uwazi_mappers import to_template, to_thesauri_snapshot
from uwazi_api.client import UwaziClient


class ProposeMappingUseCase:
    """Use case that drafts a :class:`UwaziMapping` for one run."""

    def __init__(self, client: UwaziClient, llm: LlmPort) -> None:
        self._client = client
        self._llm = llm
        self._renderer = ProposePromptRenderer()
        self._assembler = LlmDraftAssembler()
        self._filler = MappingFallbackFiller()

    async def propose_with_catalog(
        self,
        template_name: str,
        catalog: MetadataFieldCatalog,
        output_path: Path,
    ) -> UwaziMapping:
        """Run the LLM and persist the resulting draft mapping to ``output_path``."""
        template = self._resolve_template(template_name)
        thesauri_by_id = self._thesauri_by_id(template.default_language)
        agent = self._build_agent(self._llm.get_model())
        prompt = self._renderer.user_prompt(template, catalog, thesauri_by_id)
        draft = await self._run_llm(agent, prompt)
        mapping = self._assembler.assemble(draft, template)
        self._filler.apply(mapping, catalog, template, thesauri_by_id)
        self._write_yaml(mapping, output_path)
        return mapping

    def _build_agent(self, model: Model) -> Agent:
        """Construct the pydantic-ai agent with the propose prompt + result type."""
        return Agent(
            model=model,
            system_prompt=ProposePromptRenderer.SYSTEM_PROMPT,
            output_type=LlmMappingDraft,
            retries=2,
        )

    def _resolve_template(self, name: str) -> UwaziTemplate:
        """Return the requested template or raise :class:`ValueError` when missing."""
        match = self._client.templates.get_by_name(name)
        if match is None:
            raise ValueError(f"Uwazi template {name!r} not found")
        return to_template(match)

    async def _run_llm(self, agent: Agent, prompt: str) -> LlmMappingDraft:
        """Run the propose agent on the prompt and return the parsed draft."""
        limits = UsageLimits(request_limit=MAX_LLM_CALLS)
        run = await agent.run(prompt, usage_limits=limits)
        return run.output

    def _thesauri_by_id(self, language: str) -> dict[str, ThesauriSnapshot]:
        """Return a ``thesaurus_id -> ThesauriSnapshot`` map for the prompt."""
        raw = self._client.thesauris.get(language=language) or []
        return {t.thesaurus_id: t for t in (to_thesauri_snapshot(th) for th in raw)}

    def _write_yaml(self, mapping: UwaziMapping, output_path: Path) -> None:
        """Dump the mapping to a YAML file the human can edit."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                mapping.model_dump(mode="json"),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
