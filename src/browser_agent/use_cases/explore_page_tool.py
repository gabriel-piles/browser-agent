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


def _action_summary(action: PageAction) -> str:
    """Compact human-readable summary of a :class:`PageAction` for logging.

    Examples::

        navigate:  url=https://quotes.toscrape.com
        click:     selector='.next a'
        fill:      selector='#search' value='hello'
        select:    selector='#sort' value='price'
        scroll:    scroll=200px
        wait:      wait=2.0s
        extract:   selector='.quote'
    """
    parts: list[str] = []
    if action.selector:
        parts.append(f"selector={action.selector!r}")
    if action.value is not None:
        parts.append(f"value={action.value!r}")
    if action.url:
        parts.append(f"url={action.url}")
    if action.scroll_pixels is not None:
        parts.append(f"scroll={action.scroll_pixels}px")
    if action.wait_seconds is not None:
        parts.append(f"wait={action.wait_seconds}s")
    return f"{action.action}:  {' '.join(parts)}" if parts else action.action


async def explore_page(ctx: RunContext[AgentDeps], action: PageAction) -> str:
    """Perform ``action`` in the persistent browser tab and return the result.

    The browser stays open between calls — navigate first, then click
    filters, scroll to load lazy content, fill inputs, extract links, etc.
    Each call returns the page state *after* the action: the cleaned HTML
    snapshot, the current URL, scroll height, whether the URL changed, and
    (for extract) matching elements with text+href.

    Actions:
      navigate  — open ``action.url`` (first call must be navigate).
      click     — click element matching ``action.selector`` (CSS).
      scroll     — scroll to bottom (or by ``action.scroll_pixels``).
      fill      — type ``action.value`` into ``action.selector``.
      select     — choose ``action.value`` in ``<select>`` matching selector.
      extract    — return elements matching ``action.selector`` (text+href)
                   plus the cleaned HTML so you can see surrounding context.
      wait       — sleep ``action.wait_seconds`` for AJAX to settle.
      analyze    — return a compact structured summary of the page
                   (links, buttons, inputs, headings, tables, filters)
                   with CSS selectors for each element.
      inspect    — return the HTML snippet around the element matching
                   ``action.selector`` (respects ``action.context_chars``).

    The returned text includes:
      - url_changed: true if the URL changed after the action (filter click)
      - scroll_height: document height in px (compare before/after scroll)
      - error: present if the action failed (e.g. selector not found)
    """
    session = ctx.deps.browser_session
    summary = _action_summary(action)
    async with traced_tool("explore_page", summary=summary):
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
    if snapshot.structure is not None:
        return "\n".join(_format_structure(snapshot.structure, lines))
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


def _format_structure(structure: PageStructure, lines: list[str]) -> list[str]:
    """Append structured analysis sections to ``lines`` and return it."""
    _append_section(lines, "# Links", structure.links, _fmt_link)
    _append_section(lines, "# Buttons", structure.buttons, _fmt_element)
    _append_section(lines, "# Form inputs", structure.inputs, _fmt_input)
    _append_section(lines, "# Headings", structure.headings, _fmt_heading)
    _append_section(lines, "# Tables", structure.tables, _fmt_table)
    _append_section(lines, "# Pagination", structure.pagination, _fmt_link)
    _append_section(lines, "# Filters", structure.filters, _fmt_element)
    return lines


def _append_section(lines: list[str], header: str, items: list[ElementInfo], formatter) -> None:
    """Append a section header + one formatted line per item."""
    if not items:
        return
    lines.append("")
    lines.append(f"{header} ({len(items)} total):")
    for item in items:
        formatter(lines, item)


def _selector_suffix(el: ElementInfo) -> str:
    """Return the selector suffix string for an element, or empty."""
    return f" {el.selector}" if el.selector else ""


def _fmt_link(lines: list[str], el: ElementInfo) -> None:
    """Format one link element."""
    href = el.href[:200]
    lines.append(f"  <a{_selector_suffix(el)}> href={href!r} text={el.text[:120]!r}")


def _fmt_element(lines: list[str], el: ElementInfo) -> None:
    """Format one generic element (button, filter)."""
    lines.append(f"  <{el.tag}{_selector_suffix(el)}> text={el.text[:120]!r}")


def _fmt_input(lines: list[str], el: ElementInfo) -> None:
    """Format one form input element with its extra attrs."""
    extra = " ".join(f"{k}={v!r}" for k, v in sorted(el.extra.items()) if v)
    suffix = f" ({extra})" if extra else ""
    lines.append(f"  <{el.tag}{_selector_suffix(el)}>{suffix} text={el.text[:120]!r}")


def _fmt_heading(lines: list[str], el: ElementInfo) -> None:
    """Format one heading element with its level."""
    level = el.extra.get("level", "")
    lines.append(f"  {el.tag}{level}: {el.text[:120]!r}")


def _fmt_table(lines: list[str], el: ElementInfo) -> None:
    """Format one table element with row/column counts."""
    rows = el.extra.get("rows", "?")
    cols = el.extra.get("columns", "")
    suffix = f" | columns: {cols}" if cols else ""
    lines.append(f"  <table{_selector_suffix(el)}> {rows} rows{suffix}")
