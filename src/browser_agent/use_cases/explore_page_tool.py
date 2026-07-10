"""The ``explore_page`` tool bound to the Pydantic-AI agent.

Replaces the old ``inspect_html`` tool. Instead of a passive
read-only HTML snapshot, this tool drives a *persistent* browser
session: the agent passes a :class:`PageAction` (navigate, click,
scroll, fill, select, extract, wait) and gets back a
:class:`PageSnapshot` describing the page state after the action.

The browser session is shared across all calls for the lifetime of
one agent run (stored in :class:`AgentDeps`), so the agent can
navigate once, then click filters and scroll in the same tab to
explore the page's behaviour before writing any validation script.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.page_action import PageAction
from browser_agent.domain.page_snapshot import PageSnapshot
from browser_agent.use_cases.agent_deps import AgentDeps


async def explore_page(ctx: RunContext[AgentDeps], action: PageAction) -> str:
    """Perform ``action`` in the persistent browser tab and return the result.

    The browser stays open between calls — navigate first, then click
    filters, scroll to load lazy content, extract links, etc. Each call
    returns the page state *after* the action: the cleaned HTML snapshot,
    the current URL, scroll height, whether the URL changed, and (for
    extract) matching elements with text+href.

    Actions:
      navigate  — open ``action.url`` (first call must be navigate).
      click     — click element matching ``action.selector`` (CSS).
      scroll     — scroll to bottom (or by ``action.scroll_pixels``).
      fill      — type ``action.value`` into ``action.selector``.
      select     — choose ``action.value`` in ``<select>`` matching selector.
      extract    — return elements matching ``action.selector`` (text+href)
                   plus the cleaned HTML so you can see surrounding context.
      wait       — sleep ``action.wait_seconds`` for AJAX to settle.

    The returned text includes:
      - url_changed: true if the URL changed after the action (filter click)
      - scroll_height: document height in px (compare before/after scroll)
      - error: present if the action failed (e.g. selector not found)
    """
    session = ctx.deps.browser_session
    async with traced_tool("explore_page"):
        snapshot: PageSnapshot = await session.perform(action)
    return _format_snapshot(snapshot)


def _format_snapshot(snapshot: PageSnapshot) -> str:
    lines = [
        f"# Action: {snapshot.action_performed}",
        f"# URL: {snapshot.url}",
    ]
    if snapshot.title:
        lines.append(f"# Title: {snapshot.title}")
    if snapshot.summary:
        lines.append(f"# {snapshot.summary}")
    if snapshot.url_changed:
        lines.append(f"# URL CHANGED: {snapshot.previous_url} -> {snapshot.url}")
    if snapshot.scroll_height:
        lines.append(f"# scroll_height: {snapshot.scroll_height}px")
    if snapshot.error:
        lines.append(f"# ERROR: {snapshot.error}")
        return "\n".join(lines)
    if snapshot.extracted:
        lines.append("")
        lines.append(f"# Extracted elements ({snapshot.extracted_count} total):")
        for el in snapshot.extracted:
            href_part = f" href={el.href!r}" if el.href else ""
            lines.append(f"  <{el.tag}>{href_part} text={el.text!r}")
    if snapshot.cleaned_html:
        lines.append("")
        lines.append(snapshot.cleaned_html)
    return "\n".join(lines)
