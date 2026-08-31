"""Flow Writer agent: adapt the legacy Script Builder to a FlowSubtaskSpec.

Reuses the legacy :class:`ScriptBuilderUseCase` (builder system prompt,
explore + validation tools, repair turns) unchanged. The only
adaptation is the input contract: the flow explorer emits a
:class:`FlowSubtaskSpec`, and the writer's scripts are always
``kind="processing"`` — script-type discovery no longer exists.
"""

from __future__ import annotations

from typing import Any

from browser_agent.domain.flow_subtask_spec import FlowSubtaskSpec
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.subtask_spec import SubtaskSpec
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.script_builder_use_case import ScriptBuilderUseCase


class FlowWriterUseCase(ScriptBuilderUseCase):
    """Write one split's processing script via the legacy builder agent."""

    def __init__(self, deps: AgentDeps) -> None:
        super().__init__(deps)

    async def execute_spec(self, spec: FlowSubtaskSpec, context: str = "") -> GeneratedScript:
        """Run the legacy builder prompt with the flow spec as its subtask."""
        return await super().execute(self.to_legacy_spec(spec), context)

    @staticmethod
    def to_legacy_spec(spec: FlowSubtaskSpec) -> SubtaskSpec:
        """Adapt a FlowSubtaskSpec to the legacy builder's SubtaskSpec input."""
        return SubtaskSpec(
            subtask_id=spec.subtask_id,
            kind="processing",
            description=spec.description,
            verified_selectors=spec.verified_selectors,
            field_specs=spec.field_specs,
            row_selector=spec.row_selector,
            sample_document_urls=spec.sample_document_urls,
            pdf_download_strategy=spec.pdf_download_strategy,
            expected_document_count=spec.expected_document_count,
        )

    @staticmethod
    def coerce(script: Any) -> GeneratedScript:  # noqa: ANN401 — mirrors legacy typing
        """Force the emitted script kind to processing (discovery removed)."""
        if isinstance(script, GeneratedScript):
            return script.model_copy(update={"kind": "processing"})
        raise RuntimeError(f"Writer returned an unsupported output type: {type(script).__name__}")
