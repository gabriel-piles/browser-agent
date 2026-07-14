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
from typing import Any

from loguru import logger

import zendriver as zd
from zendriver.cdp import network

from browser_agent.adapters.cdp_page_tracker import CdpPageTracker
from browser_agent.adapters.zendriver_page_wait import ZendriverPageWait
from browser_agent.configuration import (
    BROWSER_TAB_LOAD_TIMEOUT_SECONDS,
    BROWSER_TAB_OPEN_TIMEOUT_SECONDS,
    PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
    PAGE_LOAD_TIMEOUT_SECONDS,
    PAGE_LOAD_WAIT_UNTIL,
    ZENDRIVER_STEALTH_ARGS,
)
from browser_agent.domain.html_snippet import HtmlSnippet
from browser_agent.domain.page_action import PageAction
from browser_agent.domain.page_snapshot import ExtractedElement, PageSnapshot
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.zendriver_patch import apply as _apply_zendriver_patch

_apply_zendriver_patch()

_DEFAULT_SETTLE_SECONDS = 3.0
_DEFAULT_WAIT_SECONDS = 1.0
_EXTRACT_MAX_ELEMENTS = 50

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
"""


class ZendriverBrowserSession(BrowserSessionPort):
    """One persistent Chrome tab the agent drives across multiple actions."""

    def __init__(self, headless: bool = False) -> None:
        self._headless = headless
        self._browser: zd.Browser | None = None
        self._tab: zd.Tab | None = None
        self._tracker: CdpPageTracker | None = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        logger.info("starting zendriver browser (headless={})", self._headless)
        self._browser = await zd.start(headless=self._headless, browser_args=ZENDRIVER_STEALTH_ARGS)
        self._tab = self._browser.main_tab
        if self._tab is None:
            raise RuntimeError("zendriver started without a main tab")
        await self._inject_stealth()
        self._tracker = CdpPageTracker(self._tab)
        await self._tracker.attach()

    async def _inject_stealth(self) -> None:
        """Inject JS to mask automation fingerprint on every new document."""
        from zendriver.cdp import page

        await self._tab.send(page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))

    async def close(self) -> None:
        browser = self._browser
        self._browser = None
        self._tab = None
        if self._tracker is not None:
            self._tracker.detach()
        self._tracker = None
        if browser is not None:
            await self._safely_stop(browser)

    async def perform(self, action: PageAction) -> PageSnapshot:
        if self._tab is None:
            raise RuntimeError("browser session not started")
        handler = self._handler_for(action.action)
        return await handler(action)

    async def get_cookies(self, urls: list[str] | None = None) -> list[dict[str, str]]:
        """Return browser cookies as dicts for curl_cffi."""
        if self._tab is None:
            return []
        try:
            cdp_cookies = await self._tab.send(network.get_cookies(urls))
        except Exception:
            logger.warning("get_cookies failed, returning empty list")
            return []
        return [_cookie_to_dict(c) for c in cdp_cookies]

    def _handler_for(self, action: str) -> Any:
        return {
            "navigate": self._do_navigate,
            "click": self._do_click,
            "scroll": self._do_scroll,
            "fill": self._do_fill,
            "select": self._do_select,
            "extract": self._do_extract,
            "wait": self._do_wait,
        }[action]

    async def _do_navigate(self, action: PageAction) -> PageSnapshot:
        url = action.url or ""
        if not url:
            return self._error_snapshot("navigate requires url")
        await self._navigate(url)
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        return await self._snapshot(f"navigated to {url}")

    async def _do_click(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        if not selector:
            return self._error_snapshot("click requires selector")
        pre_url = self._tab.url or ""
        pre_height = await self._scroll_height()
        element = await self._tab.query_selector(selector)
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
        snap = await self._snapshot(desc, previous_url=pre_url, previous_height=pre_height)
        snap.url_changed = False
        return snap

    async def _do_fill(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        value = action.value or ""
        if not selector:
            return self._error_snapshot("fill requires selector")
        pre_url = self._tab.url or ""
        element = await self._tab.query_selector(selector)
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
        element = await self._tab.query_selector(selector)
        if element is None:
            return self._error_snapshot(f"select: no element matches {selector!r}")
        await element.select_option(value)
        await self._settle(_DEFAULT_SETTLE_SECONDS)
        return await self._snapshot(
            f"selected {value!r} in {selector!r}",
            previous_url=pre_url,
        )

    async def _do_extract(self, action: PageAction) -> PageSnapshot:
        selector = action.selector or ""
        if not selector:
            return self._error_snapshot("extract requires selector")
        elements = await self._tab.query_selector_all(selector)
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
        await self._settle(seconds)
        return await self._snapshot(f"waited {seconds:.1f}s")

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
    """Convert a CDP Cookie object to a plain dict for curl_cffi."""
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
