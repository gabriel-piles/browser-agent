"""The ``inspect_html`` tool bound to the Pydantic-AI agent.

The tool is a thin async function that takes a URL, delegates the
real work to the injected :class:`WebInspectorPort`, and returns
the cleaned snippet as plain text. Returning text (instead of a
dict) keeps the agent's tool call small and unambiguous.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.html_snippet import HtmlSnippet
from browser_agent.ports.web_inspector_port import WebInspectorPort
from browser_agent.use_cases.agent_deps import AgentDeps


async def inspect_html(ctx: RunContext[AgentDeps], url: str) -> str:
    """Visit ``url`` with a browser and return a token-optimised snippet.

    Use this tool FIRST, on the target URL, before writing any code.
    The returned text contains the page's structure (after scripts,
    styles, svgs, paths and noscripts are stripped) and is the only
    authoritative source for selectors, form names and button labels.
    """
    inspector: WebInspectorPort = ctx.deps.inspector
    async with traced_tool("inspect_html"):
        snippet: HtmlSnippet = await inspector.inspect(url)
    body = snippet.cleaned_html
    header = f"# Page snapshot: {snippet.summary}\n\n"
    return header + body
