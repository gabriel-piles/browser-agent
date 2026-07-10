from __future__ import annotations

import asyncio
from loguru import logger

import zendriver as zd

from browser_agent.adapters.cdp_page_tracker import CdpPageTracker
from browser_agent.adapters.zendriver_page_wait import ZendriverPageWait
from browser_agent.configuration import (
    BROWSER_TAB_LOAD_TIMEOUT_SECONDS,
    BROWSER_TAB_OPEN_TIMEOUT_SECONDS,
    PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
    PAGE_LOAD_TIMEOUT_SECONDS,
    PAGE_LOAD_WAIT_UNTIL,
    ZENDRIVER_PROBE_TIMEOUT_S,
)
from browser_agent.domain.html_snippet import HtmlSnippet
from browser_agent.ports.web_inspector_port import WebInspectorPort
from browser_agent.zendriver_patch import apply as _apply_zendriver_patch

_apply_zendriver_patch()


class ZendriverWebInspectorAdapter(WebInspectorPort):
    """A :class:`WebInspectorPort` that drives a visible zendriver session.

    Launches a fresh Chrome for every :meth:`inspect` call (visible by
    default so the operator can watch it work), navigates to the target
    URL, waits for the page to settle using the CDP-event strategy
    ported from ``scrape-to-uwazi`` (frame-stopped-loading + network
    idle + optional settle) and then hands the raw HTML to
    :class:`HtmlSnippet` for cleaning. The browser is always torn down
    — even on exception — so a flaky site can never leak Chrome
    processes.
    """

    def __init__(self, headless: bool = False) -> None:
        self._headless = headless

    async def inspect(self, url: str, settle_seconds: float = 2.0) -> HtmlSnippet:
        browser = await zd.start(headless=self._headless)
        try:
            tab = browser.main_tab
            if tab is None:
                raise RuntimeError("zendriver started without a main tab")
            raw_html = await self._navigate_and_collect(tab, url, settle_seconds)
        finally:
            await self._safely_stop(browser)
        return HtmlSnippet.from_raw(url=url, raw_html=raw_html or "")

    async def _navigate_and_collect(
        self,
        tab: zd.Tab,
        url: str,
        settle_seconds: float,
    ) -> str:
        """Navigate, wait for the page via CDP events, then read the HTML."""
        tracker = CdpPageTracker(tab)
        tracker.begin_navigation(None)
        try:
            await tracker.attach()
            await asyncio.wait_for(
                tab.get(url), timeout=BROWSER_TAB_OPEN_TIMEOUT_SECONDS
            )
            tracker.begin_navigation(None, clear_main_document_state=False)
            page_wait = ZendriverPageWait(
                tab=tab,
                tracker=tracker,
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
            if settle_seconds > 0:
                await tab.sleep(settle_seconds)
            return await tab.get_content()
        finally:
            try:
                tracker.detach()
            except Exception:
                pass

    @staticmethod
    async def _safely_stop(browser: zd.Browser) -> None:
        """Tear the browser down, never raising over the caller's error."""
        try:
            await browser.stop()
        except Exception:
            logger.exception("failed to stop zendriver browser")