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

from typing import Protocol

from loguru import logger
from pydantic_ai import RunContext
from browser_agent.adapters.browser.page_analyzer import anchor_hrefs
from browser_agent.agent_logging import traced_tool
from browser_agent.configuration import MAX_EMPTY_EXPLORE_RESULTS, SNAPSHOT_LINK_LINES
from browser_agent.domain.element_info import ElementInfo
from browser_agent.domain.link_pattern import LinkPattern
from browser_agent.domain.page_action import PageAction
from browser_agent.domain.page_structure import PageStructure
from browser_agent.domain.page_snapshot import PageSnapshot
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.explore_duplicate_guard import action_key, suppression_message


class ExploreBudget(Protocol):
    """Budget/state attributes the explore-loop guards read from deps.

    Both :class:`AgentDeps` and :class:`VerificationAgentDeps` carry these,
    so the loop-guard helpers are shared across agents instead of duplicated.
    """

    explore_calls: int
    explore_limit: int
    empty_result_streak: int
    last_analyze_selectors: list[str]


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
    if action.select_by != "value":
        parts.append(f"select_by={action.select_by}")
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
    deps = ctx.deps
    deps.explore_calls += 1
    if deps.explore_calls > deps.explore_limit:
        return _explore_limit_reached(deps)
    session = deps.browser_session
    summary = _action_summary(action)
    key = action_key(action)
    if deps.explore_guard.check(key):
        deps.explore_guard.suppressed += 1
        return suppression_message()
    deps.explore_guard.remember(key)
    async with traced_tool("explore_page", summary=summary):
        snapshot: PageSnapshot = await session.perform(action)
    if snapshot.error:
        logger.warning(
            "explore_page ERROR — {action}: {error}",
            action=summary,
            error=snapshot.error,
        )
    empty = _is_empty_result(action, snapshot)
    if empty:
        deps.empty_result_streak += 1
    else:
        deps.empty_result_streak = 0
    if action.action == "analyze" and snapshot.structure is not None:
        deps.last_analyze_selectors = [p.selector for p in snapshot.structure.link_patterns]
    if deps.empty_result_streak >= MAX_EMPTY_EXPLORE_RESULTS:
        return _empty_result_directive(deps)
    result = _format_snapshot(snapshot)
    if empty:
        result += _empty_result_hint(deps)
    return result + _budget_footer(deps)


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
        lines.append(f"# Extracted elements ({snapshot.extracted_count} total, {len(snapshot.extracted)} shown):")
        for el in snapshot.extracted:
            href_part = f" href={el.href!r}" if el.href else ""
            lines.append(f"  <{el.tag}>{href_part} text={el.text!r}")
    if snapshot.cleaned_html:
        if not snapshot.structure and not snapshot.extracted:
            links = anchor_hrefs(snapshot.cleaned_html, snapshot.url, SNAPSHOT_LINK_LINES)
            if links:
                lines.append("")
                lines.append(f"# Page links ({len(links)} shown):")
                lines.extend(f"  href={href!r} text={text!r}" for href, text in links)
        lines.append("")
        lines.append(snapshot.cleaned_html)
    return "\n".join(lines)


def _format_structure(structure: PageStructure, lines: list[str]) -> list[str]:
    """Append structured analysis sections to ``lines`` and return it."""
    _append_link_patterns(lines, structure.link_patterns)
    links_header = "# Links"
    if structure.link_total > len(structure.links):
        links_header = (
            f"# Links ({len(structure.links)} of {structure.link_total} anchors on page — "
            "refine the selector to see the rest)"
        )
    _append_section(lines, links_header, structure.links, _fmt_link)
    _append_section(lines, "# Buttons", structure.buttons, _fmt_element)
    _append_section(lines, "# Form inputs", structure.inputs, _fmt_input)
    _append_section(lines, "# Headings", structure.headings, _fmt_heading)
    _append_section(lines, "# Tables", structure.tables, _fmt_table)
    _append_section(lines, "# Pagination", structure.pagination, _fmt_link)
    _append_section(lines, "# Filters", structure.filters, _fmt_element)
    return lines


def _append_link_patterns(lines: list[str], patterns: list[LinkPattern]) -> None:
    """Append the link-URL-patterns section, sorted by count descending."""
    if not patterns:
        return
    lines.append("")
    lines.append(f"# Link URL patterns ({len(patterns)} groups):")
    for pat in patterns:
        lines.append(f"  {pat.selector}  count={pat.count}")
        for sample in pat.sample_hrefs:
            lines.append(f"    {sample}")


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


def _budget_footer(deps: ExploreBudget) -> str:
    """Return a pacing footer telling the model how many explore calls remain."""
    remaining = deps.explore_limit - deps.explore_calls
    return f"\n# exploration call {deps.explore_calls}; {remaining} explore calls remain before you must emit."


def _is_empty_result(action: PageAction, snapshot: PageSnapshot) -> bool:
    """True when the action produced no usable elements (a dead selector)."""
    if action.action == "extract":
        return snapshot.extracted_count == 0 and not snapshot.error
    if action.action == "inspect":
        return bool(snapshot.error)
    return False


def _available_selectors(deps: ExploreBudget) -> list[str]:
    """Return the cached link-pattern selectors from the last analyze, else [].

    ``link_patterns`` selectors are guaranteed to match at least 2 links
    (groups with fewer are not emitted), so they are a correct-by-construction
    oracle the agent can use instead of blind-probing.
    """
    return deps.last_analyze_selectors


def _empty_result_hint(deps: ExploreBudget) -> str:
    """Append the analyze oracle after a dead selector, before the streak hard-stop."""
    selectors = _available_selectors(deps)
    if not selectors:
        return (
            "\n# NOTE: your selector matched nothing on this page. "
            "Call explore_page(action='analyze') to see the real structure."
        )
    lines = [
        "\n# NOTE: your extract/inspect selector matched nothing on this page.",
        "# Verified selectors from the last analyze (guaranteed to match):",
    ]
    lines.extend(f"  {s}" for s in selectors)
    lines.append("# Use one of these — do NOT retry the failing selector.")
    return "\n".join(lines)


def _empty_result_directive(deps: ExploreBudget) -> str:
    """Refuse further blind probing and offer the analyze oracle."""
    selectors = _available_selectors(deps)
    lines = [
        f"# STOP — {deps.empty_result_streak} consecutive queries returned no elements.",
        "Your recent extract/inspect selectors match nothing on the current page.",
        "Do NOT retry the same selector.",
    ]
    if selectors:
        lines.append("")
        lines.append("# Verified selectors from the last analyze (guaranteed to match):")
        for sel in selectors:
            lines.append(f"  {sel}")
        lines.append("Use one of these, or call explore_page(action='analyze') to refresh the list.")
    else:
        lines.append("Call explore_page(action='analyze') to see the actual page structure,")
        lines.append("then pick a selector that exists — or emit your final result now.")
    return "\n".join(lines)


def _explore_limit_reached(deps: ExploreBudget) -> str:
    return (
        f"# explore_page limit reached ({deps.explore_limit}/{deps.explore_limit}).\n"
        "You have used all your exploration calls. STOP calling this tool.\n"
        "Emit your final result now using the selectors and mechanics you have already verified."
    )
