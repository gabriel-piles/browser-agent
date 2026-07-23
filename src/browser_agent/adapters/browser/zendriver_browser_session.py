"""A :class:`BrowserSessionPort` backed by a persistent zendriver Chrome.

Unlike the old :class:`ZendriverWebInspectorAdapter` (which launched a
fresh Chrome per call), this adapter keeps one browser instance alive
for the lifetime of the agent run. The agent can navigate, click
filters, scroll to load lazy content, fill inputs, and extract
elements — all in the same tab — *before* writing any validation
script.

The session is started once (``start``), driven repeatedly
(``perform``), and torn down at the end (``close``). The browser is
always cleaned up, even on exception.

Every action captures the URL before and after, the scroll height
after, and any error — so the agent can detect whether the page
reacted (URL changed, height grew) without guessing.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from zendriver.cdp import network
from zendriver.core.connection import ProtocolException as _ProtocolException
from bs4 import BeautifulSoup, Tag

from browser_agent.adapters.browser.clean_browser_launcher import (
    connect_and_prepare,
    free_port,
    launch_chromium,
    stop_browser,
)
from browser_agent.adapters.cdp_page_tracker import CdpPageTracker
from browser_agent.adapters.human_challenge_detector import (
    BypassConfig,
    HumanChallengeBypass,
    UnsolvedChallengeError,
    HumanChallengeBypass as _HumanChallengeBypass,
)
from browser_agent.adapters.zendriver_page_wait import ZendriverPageWait
from browser_agent.configuration import (
    ANALYZE_MAX_BUTTONS,
    ANALYZE_MAX_HEADINGS,
    ANALYZE_MAX_INPUTS,
    ANALYZE_MAX_LINKS,
    ANALYZE_MAX_TABLES,
    BROWSER_TAB_LOAD_TIMEOUT_SECONDS,
    BROWSER_TAB_OPEN_TIMEOUT_SECONDS,
    PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
    PAGE_LOAD_TIMEOUT_SECONDS,
    PAGE_LOAD_WAIT_UNTIL,
)

from browser_agent.domain.element_info import ElementInfo
from browser_agent.domain.page_action import PageAction
from browser_agent.domain.html_snippet import HtmlSnippet
from browser_agent.domain.page_snapshot import ExtractedElement, PageSnapshot
from browser_agent.domain.page_structure import PageStructure
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.zendriver_patch import apply as _apply_zendriver_patch

_apply_zendriver_patch()

_DEFAULT_SETTLE_SECONDS = 3.0
_DEFAULT_WAIT_SECONDS = 1.0
_EXTRACT_MAX_ELEMENTS = 50


def _build_selector(tag_name: str, attrs: dict[str, Any]) -> str:
    """Build a simple CSS selector from tag + attributes (BS4 attrs)."""
    raw_id = attrs.get("id") or ""
    el_id: str = raw_id if isinstance(raw_id, str) else ""
    if el_id:
        return f"{tag_name}#{el_id}"
    raw_class = attrs.get("class")
    if raw_class:
        classes = raw_class if isinstance(raw_class, list) else raw_class.split()
        if classes:
            return f"{tag_name}.{'.'.join(classes[:2])}"
    raw_name = attrs.get("name")
    el_name: str = raw_name if isinstance(raw_name, str) else ""
    if el_name:
        return f"{tag_name}[name={el_name}]"
    return tag_name


def _get_text(el: Tag, max_len: int = 200) -> str:
    """Get stripped text from a BeautifulSoup tag, truncated."""
    return (el.get_text(strip=True) or "")[:max_len]


def _extract_links(soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
    """Extract <a href=...> elements, skipping anchors and javascript:."""
    results: list[ElementInfo] = []
    for a in soup.find_all("a", href=True)[:limit]:
        href = a["href"]
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        results.append(
            ElementInfo(
                tag="a",
                text=_get_text(a),
                href=href,
                selector=_build_selector("a", a.attrs),
            )
        )
    return results


def _extract_buttons(soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
    """Extract <button> and input[type=submit/button] elements."""
    results: list[ElementInfo] = []
    for btn in soup.find_all(["button", "input"])[:limit]:
        tag = btn.name or "button"
        if tag == "input" and btn.get("type", "").lower() not in ("submit", "button", ""):
            continue
        text = _get_text(btn) or (btn.get("value") or "")
        extra: dict[str, str] = {}
        if tag == "input":
            btn_type = btn.get("type") or "submit"
            extra["type"] = str(btn_type)
        results.append(
            ElementInfo(
                tag=tag,
                text=text,
                selector=_build_selector(tag, btn.attrs),
                extra=extra,
            )
        )
    return results


def _extract_inputs(soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
    """Extract <input>, <select>, <textarea> form elements."""
    results: list[ElementInfo] = []
    for el in soup.find_all(["input", "select", "textarea"])[:limit]:
        tag = el.name or "input"
        if tag == "input" and el.get("type", "").lower() in ("submit", "button", "hidden"):
            continue
        extra: dict[str, str] = {}
        el_type = el.get("type")
        if el_type:
            extra["type"] = str(el_type)
        el_name = el.get("name")
        if el_name:
            extra["name"] = str(el_name)
        results.append(
            ElementInfo(
                tag=tag,
                text=_get_text(el),
                selector=_build_selector(tag, el.attrs),
                extra=extra,
            )
        )
    return results


def _extract_headings(soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
    """Extract <h1> through <h6> elements."""
    results: list[ElementInfo] = []
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])[:limit]:
        results.append(
            ElementInfo(
                tag=h.name or "h",
                text=_get_text(h),
                selector=_build_selector(h.name or "h", h.attrs),
                extra={"level": (h.name or "h")[1]},
            )
        )
    return results


def _extract_tables(soup: BeautifulSoup, limit: int) -> list[ElementInfo]:
    """Extract <table> elements with row count and column headers."""
    results: list[ElementInfo] = []
    for tbl in soup.find_all("table")[:limit]:
        rows = len(tbl.find_all("tr"))
        headers = [th.get_text(strip=True) for th in tbl.find_all("th")[:10]]
        col_desc = ", ".join(headers) if headers else f"{rows} rows"
        results.append(
            ElementInfo(
                tag="table",
                text=col_desc,
                selector=_build_selector("table", tbl.attrs),
                extra={"rows": str(rows), "columns": (", ".join(headers)) if headers else ""},
            )
        )
    return results


_PAGINATION_TEXTS = {"next", "prev", "previous", "page", "more", "»", "«", ">>", "<<"}


def _is_pagination(el: Tag) -> bool:
    """Heuristic: text, class, or href contains pagination keywords."""
    text = (el.get_text(strip=True) or "").lower()
    if text in _PAGINATION_TEXTS or any(t in text for t in ("page", "next", "prev")):
        return True
    for attr in ("class", "id"):
        raw = el.get(attr)
        if raw is None:
            continue
        val = " ".join(raw) if isinstance(raw, list) else str(raw)
        if any(t in val.lower() for t in ("page", "pagination", "pager", "next", "prev")):
            return True
    href = el.get("href", "")
    if isinstance(href, str) and ("page=" in href or "?page" in href or "&page" in href):
        return True
    return False


def _is_filter(el: Tag) -> bool:
    """Heuristic: select, checkbox, or class/text contains filter keywords."""
    if el.name == "select":
        return True
    if el.name == "input" and el.get("type", "").lower() in ("checkbox", "radio"):
        return True
    for attr in ("class", "id"):
        raw = el.get(attr)
        if raw is None:
            continue
        val = " ".join(raw) if isinstance(raw, list) else str(raw)
        if "filter" in val.lower() or "sort" in val.lower():
            return True
    text = (el.get_text(strip=True) or "").lower()
    if text in ("filter", "sort", "filter by", "sort by"):
        return True
    return False


def _selectors_summary(infos: list[ElementInfo]) -> str:
    """Join selector strings for the filters/pagination lines."""
    return ", ".join(info.selector for info in infos[:5])


def _analyze_page_structure(raw_html: str, url: str, title: str) -> PageStructure:
    """Parse HTML and build a structured :class:`PageStructure`."""
    soup = BeautifulSoup(raw_html or "", "html.parser")
    all_links = _extract_links(soup, ANALYZE_MAX_LINKS)
    all_buttons = _extract_buttons(soup, ANALYZE_MAX_BUTTONS)
    all_inputs = _extract_inputs(soup, ANALYZE_MAX_INPUTS)
    all_headings = _extract_headings(soup, ANALYZE_MAX_HEADINGS)
    all_tables = _extract_tables(soup, ANALYZE_MAX_TABLES)
    pagination: list[ElementInfo] = []
    filters: list[ElementInfo] = []
    for info in all_links:
        a_tag = soup.find("a", href=info.href) if info.href else None
        if a_tag and _is_pagination(a_tag):
            pagination.append(info)
    for info in all_buttons:
        btn = soup.find(info.tag, text=info.text) if info.tag != "input" else None
        if btn and (_is_pagination(btn) or _is_filter(btn)):
            (_pagination if _is_pagination(btn) else filters).append(info)
    for el in soup.find_all(["select", "input"])[:ANALYZE_MAX_INPUTS]:
        if _is_filter(el):
            filters.append(
                ElementInfo(
                    tag=el.name or "select",
                    text=_get_text(el),
                    selector=_build_selector(el.name or "select", el.attrs),
                    extra={"type": el.get("type", "") or "select", "name": el.get("name", "") or ""},
                )
            )
    return PageStructure(
        url=url,
        title=title,
        links=all_links,
        buttons=all_buttons,
        inputs=all_inputs,
        headings=all_headings,
        tables=all_tables,
        pagination=pagination,
        filters=filters,
    )


class ZendriverBrowserSession(BrowserSessionPort):
    """One persistent Chrome tab the agent drives across multiple actions."""

    def __init__(self, headless: bool = False, user_data_dir: Path | None = None) -> None:
        self._headless = headless
        self._user_data_dir = user_data_dir
        self._browser: zd.Browser | None = None
        self._tab: zd.Tab | None = None
        self._tracker: CdpPageTracker | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._port: int | None = None
        self._challenge_bypass = HumanChallengeBypass(
            config=BypassConfig(
                max_wait_rounds=0,
                wait_seconds=0.0,
                settle_seconds=1.0,
                click_any_checkbox=False,
                allow_reload=False,
                interactive_pause=True,
                interactive_timeout_seconds=120.0,
            )
        )

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._port = free_port()
        if self._user_data_dir is not None:
            self._user_data_dir.mkdir(parents=True, exist_ok=True)
            self._seed_profile_if_empty(self._user_data_dir)
            user_data_dir = str(self._user_data_dir)
            logger.info("launching clean Chromium (headless={}, profile={})", self._headless, user_data_dir)
        else:
            user_data_dir = tempfile.mkdtemp(prefix="zd_profile_")
            logger.info("launching clean Chromium (headless={})", self._headless)
        self._process = launch_chromium(
            port=self._port,
            user_data_dir=user_data_dir,
            headless=self._headless,
        )
        self._browser, self._tab = await connect_and_prepare(port=self._port)
        self._tracker = CdpPageTracker(self._tab)
        await self._tracker.attach()

    @staticmethod
    def _seed_profile_if_empty(profile_dir: Path) -> None:
        """Copy the real Chromium profile into ``profile_dir`` if it has no Cookies file.

        A fresh profile looks like a brand-new browser to Cloudflare.
        Seeding it with the real profile's cookies and local state
        gives the browser a real-world fingerprint from the first run.
        Subsequent runs reuse the now-warm profile.
        """
        default_dir = profile_dir / "Default"
        if (default_dir / "Cookies").exists():
            return
        real_profile = Path.home() / ".config" / "chromium"
        if not (real_profile / "Default" / "Cookies").exists():
            logger.info("no real Chromium profile to seed from")
            return
        logger.info("seeding empty profile {} from real Chromium {}", profile_dir, real_profile)
        shutil.copytree(real_profile, profile_dir, dirs_exist_ok=True, symlinks=True)

    async def close(self) -> None:
        browser = self._browser
        self._browser = None
        self._tab = None
        if self._tracker is not None:
            self._tracker.detach()
        self._tracker = None
        if browser is not None:
            await stop_browser(browser, self._process)
        self._process = None
        self._port = None

    async def perform(self, action: PageAction) -> PageSnapshot:
        if self._tab is None:
            raise RuntimeError("browser session not started")
        handler = self._handler_for(action.action)
        return await handler(action)

    async def get_cookies(self, urls: list[str] | None = None) -> list[dict[str, str]]:
        """Return browser cookies as dicts for the PDF downloader."""
        if self._tab is None:
            return []
        try:
            cdp_cookies = await self._tab.send(network.get_cookies(urls))
        except Exception:
            logger.warning("get_cookies failed, returning empty list")
            return []
        return [_cookie_to_dict(c) for c in cdp_cookies]

    async def new_tab(self) -> Any:
        """Open a fresh tab in the existing browser and return it.

        Used by the in-process validation runner so each LLM-emitted
        script runs in its own tab without spawning a new Chromium.
        Returns ``None`` if the session has not been started.
        """
        if self._browser is None:
            raise RuntimeError("browser session not started")
        # ``browser.get`` with ``new_tab=True`` opens a tab and returns it.
        return await self._browser.get("about:blank", new_tab=True)

    def _handler_for(self, action: str) -> Any:
        return {
            "navigate": self._do_navigate,
            "click": self._do_click,
            "scroll": self._do_scroll,
            "fill": self._do_fill,
            "select": self._do_select,
            "extract": self._do_extract,
            "wait": self._do_wait,
            "analyze": self._do_analyze,
            "inspect": self._do_inspect,
        }[action]

    async def _query(self, selector: str) -> Any:
        """Like ``tab.query_selector`` but returns ``None`` on a bad selector.

        CDP's ``DOM.querySelector`` raises ``ProtocolException`` for a
        non-standard CSS selector — jQuery pseudo-classes such as
        ``:contains()`` and Playwright-only ones like ``:has-text()``,
        ``:text=``, ``:visible`` (note: the relational ``:has()`` *is*
        standard CSS and is fine; ``:contains()`` is not). The agent
        sometimes emits those; we degrade to "no match" so the run stays
        alive and the agent retries with a valid selector instead of
        crashing.
        """
        try:
            return await self._tab.query_selector(selector)
        except _ProtocolException:
            logger.warning("invalid selector, treating as no match: {s}", s=selector)
            return None

    async def _query_all(self, selector: str) -> list[Any]:
        """Like ``tab.query_selector_all`` but returns ``[]`` on a bad selector."""
        try:
            return await self._tab.query_selector_all(selector)
        except _ProtocolException:
            logger.warning("invalid selector, treating as no matches: {s}", s=selector)
            return []

    async def _do_navigate(self, action: PageAction) -> PageSnapshot:
        url = action.url or ""
        if not url:
            return self._error_snapshot("navigate requires url")
        await self._navigate(url)
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        snapshot = await self._snapshot(f"navigated to {url}")
        return await self._annotate_challenge(snapshot)

    async def _do_click(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        if not selector:
            return self._error_snapshot("click requires selector")
        pre_url = self._tab.url or ""
        pre_height = await self._scroll_height()
        element = await self._query(selector)
        if element is None:
            return self._error_snapshot(f"click: no element matches {selector!r}")
        await element.click()
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        return await self._snapshot(
            f"clicked {selector!r}",
            previous_url=pre_url,
            previous_height=pre_height,
        )

    async def _do_scroll(self, action: PageAction) -> PageSnapshot:
        pre_url = self._tab.url or ""
        pre_height = await self._scroll_height()
        pixels = action.scroll_pixels
        if pixels is not None:
            await self._tab.evaluate(f"window.scrollTo(0, {pixels})")
            desc = f"scrolled {pixels}px"
        else:
            await self._tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            desc = "scrolled to bottom"
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        post_height = await self._scroll_height()
        # State-aware: scroll-to-bottom with no height change → brief return
        if pixels is None and post_height <= pre_height and pre_height > 0:
            return PageSnapshot(
                url=self._tab.url or "",
                action_performed=desc,
                scroll_height=post_height,
                summary=f"scroll height unchanged ({post_height}px) — no new content loaded",
            )
        snap = await self._snapshot(desc, previous_url=pre_url, previous_height=pre_height)
        snap.url_changed = False
        return snap

    async def _do_fill(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        value = action.value or ""
        if not selector:
            return self._error_snapshot("fill requires selector")
        pre_url = self._tab.url or ""
        element = await self._query(selector)
        if element is None:
            return self._error_snapshot(f"fill: no element matches {selector!r}")
        await element.clear_input()
        await element.send_keys(value)
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        return await self._snapshot(
            f"filled {selector!r} with {value!r}",
            previous_url=pre_url,
        )

    async def _do_select(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        value = action.value or ""
        if not selector:
            return self._error_snapshot("select requires selector")
        pre_url = self._tab.url or ""
        element = await self._query(selector)
        if element is None:
            return self._error_snapshot(f"select: no element matches {selector!r}")
        # zendriver's select_option is parameterless and only works on an
        # OPTION element. For our API we select the option whose value/label
        # matches ``value`` and then click it (which fires change events).
        option_selector = f"{selector} option[value={json.dumps(value)}]"
        option = await self._query(option_selector)
        if option is None:
            # Fallback: match by text content.
            option = await self._query(f"{selector} option")
            if option is not None and value.lower() not in (getattr(option, "text", "") or "").lower():
                option = None
        if option is None:
            return self._error_snapshot(f"select: no option matches {value!r} in {selector!r}")
        await option.click()
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        return await self._snapshot(
            f"selected {value!r} in {selector!r}",
            previous_url=pre_url,
        )

    async def _do_extract(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        if not selector:
            return self._error_snapshot("extract requires selector")
        elements = await self._query_all(selector)
        extracted = self._build_extracted(elements)
        snippet = await self._build_snippet()
        return PageSnapshot(
            url=snippet.url,
            title=await self._tab_title(),
            summary=snippet.summary,
            cleaned_html=snippet.cleaned_html,
            action_performed=f"extracted {len(extracted)} elements with {selector!r}",
            extracted=extracted,
            extracted_count=len(extracted),
            scroll_height=await self._scroll_height(),
            previous_url=snippet.url,
            url_changed=False,
        )

    async def _do_wait(self, action: PageAction) -> PageSnapshot:
        seconds = action.wait_seconds or _DEFAULT_WAIT_SECONDS
        # If the current page is a challenge, treat "wait" as a bypass attempt
        # instead of a dumb sleep. This lets the agent decide when to spend
        # time on anti-bot resolution.
        snapshot = await self._snapshot(f"waited {seconds:.1f}s")
        status = self._challenge_bypass.detector.detect(snapshot.url, snapshot.title or "", snapshot.cleaned_html or "")
        if status:
            logger.info(
                "challenge detected during wait action: kind={kind} confidence={conf:.2f}",
                kind=status.kind,
                conf=status.confidence,
            )
            try:
                await self._challenge_bypass.wait_for_clear(
                    self._tab,
                    snapshot.url,
                    snapshot.title or "",
                    snapshot.cleaned_html or "",
                )
                logger.info("challenge cleared automatically")
            except UnsolvedChallengeError as exc:
                logger.warning("challenge could not be auto-cleared: {msg}", msg=str(exc))
        else:
            await self._settle(seconds)
        return await self._snapshot(f"waited {seconds:.1f}s")

    async def _do_analyze(self, action: PageAction) -> PageSnapshot:
        raw_html = await self._tab.get_content()
        structure = _analyze_page_structure(
            raw_html=raw_html or "",
            url=self._tab.url or "",
            title=await self._tab_title(),
        )
        return PageSnapshot(
            url=structure.url,
            title=structure.title,
            action_performed="analyzed page structure",
            structure=structure,
            scroll_height=await self._scroll_height(),
        )

    async def _do_inspect(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        if not selector:
            return self._error_snapshot("inspect requires selector")
        html = await self._inspect_element(selector, action.context_chars or _DEFAULT_INSPECT_CONTEXT)
        if html is None:
            return self._error_snapshot(f"inspect: no element matches {selector!r}")
        return PageSnapshot(
            url=self._tab.url or "",
            action_performed=f"inspected {selector!r}",
            cleaned_html=html,
            scroll_height=await self._scroll_height(),
        )

    async def _inspect_element(self, selector: str, context_chars: int) -> str | None:
        """Get HTML around the element matching ``selector`` via CDP evaluate."""
        escaped = json.dumps(selector)
        result = await self._tab.evaluate(
            f"""(() => {{
                const el = document.querySelector({escaped});
                if (!el) return JSON.stringify({{error:'not found'}});
                const parts = [];
                let sib = el.previousElementSibling;
                while (sib) {{ parts.unshift(sib.outerHTML); sib = sib.previousElementSibling; }}
                parts.push(el.outerHTML);
                sib = el.nextElementSibling;
                while (sib) {{ parts.push(sib.outerHTML); sib = sib.nextElementSibling; }}
                const html = parts.join('\\n');
                if (html.length <= {context_chars})
                    return JSON.stringify({{html}});
                const idx = html.indexOf(el.outerHTML);
                const start = Math.max(0, idx - {context_chars});
                const end = Math.min(html.length, idx + el.outerHTML.length + {context_chars});
                return JSON.stringify({{html: html.slice(start, end)}});
            }})()"""
        )
        if not isinstance(result, dict) or result.get("error"):
            return None
        return result.get("html")

    async def _navigate(self, url: str) -> None:
        self._tracker.begin_navigation(None)
        await asyncio.wait_for(self._tab.get(url), timeout=BROWSER_TAB_OPEN_TIMEOUT_SECONDS)
        self._tracker.begin_navigation(None, clear_main_document_state=False)
        page_wait = ZendriverPageWait(
            tab=self._tab,
            tracker=self._tracker,
            default_strategy=PAGE_LOAD_WAIT_UNTIL,
            default_load_timeout=PAGE_LOAD_TIMEOUT_SECONDS,
            quiet_window_ms=PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
        )
        try:
            await page_wait.wait_until_ready(
                strategy="load",
                timeout=BROWSER_TAB_LOAD_TIMEOUT_SECONDS,
            )
        except Exception:
            pass

    async def _settle(self, seconds: float) -> None:
        if seconds > 0:
            await self._tab.sleep(seconds)

    async def _scroll_height(self) -> int:
        try:
            result = await self._tab.evaluate("document.body.scrollHeight")
            return int(result) if result is not None else 0
        except Exception:
            return 0

    async def _build_snippet(self) -> HtmlSnippet:
        raw_html = await self._tab.get_content()
        return HtmlSnippet.from_raw(url=self._tab.url or "", raw_html=raw_html or "")

    async def _annotate_challenge(self, snapshot: PageSnapshot) -> PageSnapshot:
        """If the snapshot looks like a challenge, append a warning to its summary.

        The agent should then call explore_page with action='wait' to trigger
        the bypass path. We do NOT block here because blocking on every
        snapshot causes the agent loop to time out.
        """
        if self._tab is None:
            return snapshot
        status = self._challenge_bypass.detector.detect(snapshot.url, snapshot.title or "", snapshot.cleaned_html or "")
        if not status:
            return snapshot
        warning = (
            f"CHALLENGE DETECTED ({status.kind}, confidence {status.confidence:.0%}). "
            f"Call explore_page(action='wait', wait_seconds=10) to attempt auto-bypass. "
            f"Indicators: {'; '.join(status.details[:3])}."
        )
        snapshot.summary = ((snapshot.summary or "") + "\n" + warning).strip()
        return snapshot

    async def _snapshot(
        self,
        action_desc: str,
        previous_url: str = "",
        previous_height: int | None = None,
    ) -> PageSnapshot:
        snippet = await self._build_snippet()
        height = await self._scroll_height()
        changed = bool(previous_url) and previous_url != snippet.url
        return PageSnapshot(
            url=snippet.url,
            title=await self._tab_title(),
            summary=snippet.summary,
            cleaned_html=snippet.cleaned_html,
            action_performed=action_desc,
            scroll_height=height,
            previous_url=previous_url,
            url_changed=changed,
        )

    def _error_snapshot(self, message: str) -> PageSnapshot:
        return PageSnapshot(
            url=self._tab.url or "",
            action_performed="error",
            error=message,
        )

    async def _tab_title(self) -> str:
        try:
            return await self._tab.evaluate("document.title") or ""
        except Exception:
            return ""

    @staticmethod
    def _build_extracted(elements: list[Any]) -> list[ExtractedElement]:
        results: list[ExtractedElement] = []
        for el in elements[:_EXTRACT_MAX_ELEMENTS]:
            tag = getattr(el, "tag_name", "") or ""
            text = (getattr(el, "text", None) or "").strip()[:500]
            href = ""
            try:
                href = el.attrs.get("href", "") or ""
            except Exception:
                pass
            results.append(ExtractedElement(tag=tag, text=text, href=href))
        return results

    @staticmethod
    async def _safely_stop(browser: zd.Browser) -> None:
        try:
            await browser.stop()
        except Exception:
            logger.exception("failed to stop zendriver browser")


def _cookie_to_dict(c: Any) -> dict[str, str]:
    """Convert a CDP Cookie object to a plain dict for the PDF downloader."""
    same_site = getattr(c.same_site, "value", str(c.same_site)) if c.same_site else ""
    return {
        "name": c.name,
        "value": c.value,
        "domain": c.domain,
        "path": c.path,
        "expires": c.expires,
        "http_only": c.http_only,
        "secure": c.secure,
        "same_site": same_site.lower() if same_site else "",
    }
