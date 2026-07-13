from __future__ import annotations

from dataclasses import dataclass

from browser_agent.configuration import MAX_VALIDATION_ATTEMPTS
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.ports.llm_port import LlmPort
from browser_agent.ports.pdf_downloader_port import PdfDownloaderPort
from browser_agent.ports.script_runner_port import ScriptRunnerPort


@dataclass
class AgentDeps:
    """The dependency object every agent receives on its ``RunContext``.

    Carries the provider-agnostic :class:`LlmPort` (the use case
    ignores it — pydantic-ai wires the model into the agent directly),
    the :class:`BrowserSessionPort` that powers the ``explore_page``
    tool, the :class:`ScriptRunnerPort` that powers the
    ``run_validation_script`` tool, and the :class:`PdfDownloaderPort`
    that powers the ``download_pdf`` tool. The agent and its tools
    share these single instances for the lifetime of one ``execute``
    call.

    ``validation_attempts`` and ``validation_limit`` track how many
    validation scripts the agent has run so the tool can refuse
    further runs once the budget is exhausted — the system prompt
    says "max 3" but the LLM ignores prose limits, so this is the
    hard backstop.
    """

    llm: LlmPort
    browser_session: BrowserSessionPort
    script_runner: ScriptRunnerPort
    pdf_downloader: PdfDownloaderPort
    validation_attempts: int = 0
    validation_limit: int = MAX_VALIDATION_ATTEMPTS
