from __future__ import annotations

from dataclasses import dataclass

from browser_agent.ports.llm_port import LlmPort
from browser_agent.ports.script_runner_port import ScriptRunnerPort
from browser_agent.ports.web_inspector_port import WebInspectorPort

@dataclass
class AgentDeps:
    """The dependency object every agent receives on its ``RunContext``.

    Carries the provider-agnostic :class:`LlmPort` (the use case
    ignores it — pydantic-ai wires the model into the agent directly),
    the :class:`WebInspectorPort` that powers the ``inspect_html``
    tool, and the :class:`ScriptRunnerPort` that powers the
    ``run_validation_script`` tool. The agent and its tools share
    these single instances for the lifetime of one ``execute`` call.
    """

    llm: LlmPort
    inspector: WebInspectorPort
    script_runner: ScriptRunnerPort
