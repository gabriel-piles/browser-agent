from __future__ import annotations

from dataclasses import dataclass

from browser_agent.ports.llm_port import LlmPort
from browser_agent.ports.web_inspector_port import WebInspectorPort


@dataclass
class AgentDeps:
    """The dependency object every agent receives on its ``RunContext``.

    Carries the provider-agnostic :class:`LlmPort` (the use case
    ignores it — pydantic-ai wires the model into the agent directly)
    and the :class:`WebInspectorPort` that powers the ``inspect_html``
    tool. The agent and its tool share this single instance for the
    lifetime of one ``execute`` call.
    """

    llm: LlmPort
    inspector: WebInspectorPort
