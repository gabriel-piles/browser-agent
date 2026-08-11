"""The canonical link-discovery loop for emitted scripts.

The agent imports :func:`discover_links` instead of hand-writing the
scroll / load-more / termination loop — same pattern as
``select_filter_value``, ``trusted_click``, ``wait_for_anchors``.
Ported from the oracle ``robust_discover`` (the verification script's
independent full scroll + load-more + retry loop). Stdlib-only; takes
a zendriver ``tab`` duck-typed so no ``import zendriver`` is needed.
"""

from __future__ import annotations

import json
from urllib.parse import urljoin, quote

from script_tools.dom_helpers import trusted_click
from script_tools.page_wait import wait_for_anchors

_DEFAULT_SCROLL_JS = (
    "(function(){var els=Array.from(document.querySelectorAll("
    "'main.main__container,.search-results-container,.ja.main_column,"
    "[class*=smart-scroll]'));for(var i=0;i<els.length;i++){var e=els[i];"
    "if(e.scrollHeight>e.clientHeight+5){e.scrollTop=e.scrollHeight;"
    "return e.scrollHeight;}}window.scrollTo(0,document.body.scrollHeight);"
    "return document.body.scrollHeight;})()"
)


def _origin(url: str) -> str:
    """Return the scheme://host origin of ``url`` (empty on miss)."""
    if not url:
        return ""
    for sep in ("://",):
        if sep not in url:
            return ""
    try:
        scheme, rest = url.split("://", 1)
    except ValueError:
        return ""
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}" if host else ""


async def _collect_hrefs(tab, link_selector: str) -> list[str]:
    """Return raw href strings for ``link_selector`` via one evaluate."""
    js = (
        "JSON.stringify(Array.from(document.querySelectorAll("
        + json.dumps(link_selector)
        + ")).map(a=>a.getAttribute('href')||''))"
    )
    raw = await tab.evaluate(js)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


async def _load_more_visible(tab, selector: str) -> bool:
    """True while ``selector`` exists and is visible/clickable."""
    if not selector:
        return False
    js = (
        "(()=>{var s=document.querySelector(" + json.dumps(selector) + ");"
        "if(!s){return false;}var r=s.getBoundingClientRect();"
        "if(r.width===0||r.height===0){return false;}"
        "return getComputedStyle(s).display!=='none';})()"
    )
    try:
        return bool(await tab.evaluate(js))
    except Exception:
        return False


async def _absolutize(tab, hrefs: list[str], base_url: str) -> list[str]:
    """Dedup ``hrefs`` to absolute URLs via ``urljoin(base, quote(href))``."""
    base = base_url or _origin(getattr(tab, "url", "") or "")
    seen: set[str] = set()
    out: list[str] = []
    for href in hrefs:
        if not href:
            continue
        abs_url = urljoin(base, quote(href.strip(), safe="/%?=&"))
        if abs_url not in seen:
            seen.add(abs_url)
            out.append(abs_url)
    return out


async def discover_links(
    tab,
    link_selector: str,
    load_more_selector: str = "",
    advertised: int = 0,
    base_url: str = "",
    scroll_js: str = "",
    max_rounds: int = 12,
) -> list[str]:
    """Collect all ``link_selector`` hrefs via scroll + load-more.

    Each round: scroll → click load-more if visible → wait for anchors
    → collect hrefs. Retries the click once on no-growth-while-control-
    visible (overlay intercept). Terminates when ``count >= advertised``
    (when advertised > 0) OR (load-more gone AND 3 stable no-growth
    rounds). NEVER stops on no-growth while the control is present and
    ``count < advertised``. Returns deduplicated absolute URLs.
    """
    scroll = scroll_js or _DEFAULT_SCROLL_JS
    links: list[str] = []
    prev = 0
    stable = 0
    for _ in range(max_rounds):
        await tab.evaluate(scroll)
        await tab.sleep(1.5)
        more = await _load_more_visible(tab, load_more_selector)
        if more:
            await trusted_click(tab, load_more_selector)
            await tab.sleep(1.0)
            try:
                await wait_for_anchors(tab, link_selector, timeout=10)
            except Exception:
                pass
        links = await _absolutize(tab, await _collect_hrefs(tab, link_selector), base_url)
        count = len(links)
        if count == prev and more:
            await trusted_click(tab, load_more_selector)
            await tab.sleep(1.0)
            try:
                await wait_for_anchors(tab, link_selector, timeout=10)
            except Exception:
                pass
            links = await _absolutize(tab, await _collect_hrefs(tab, link_selector), base_url)
            count = len(links)
        if count == prev:
            stable += 1
        else:
            stable = 0
        prev = count
        more_after = await _load_more_visible(tab, load_more_selector)
        if (advertised > 0 and count >= advertised) or (stable >= 3 and not more_after):
            break
    return links
