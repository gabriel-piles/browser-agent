from __future__ import annotations

import asyncio
from loguru import logger

import zendriver as zd

from browser_agent.configuration import ZENDRIVER_PROBE_TIMEOUT_S
from browser_agent.domain.html_snippet import HtmlSnippet
from browser_agent.ports.web_inspector_port import WebInspectorPort


class ZendriverWebInspectorAdapter(WebInspectorPort):
    """A :class:`WebInspectorPort` that drives a visible zendriver session.

    Launches a fresh Chrome for every :meth:`inspect` call (visible by
    default so the operator can watch it work), navigates to the target
    URL, sleeps long enough for the SPA to render and then hands the raw
    HTML to :class:`HtmlSnippet` for cleaning. The browser is always
    torn down — even on exception — so a flaky site can never leak
    Chrome processes.
    """

    def __init__(self, headless: bool = False) -> None:
        self._headless = headless

    async def inspect(self, url: str, settle_seconds: float = 2.0) -> HtmlSnippet:
        browser = await zd.start(headless=self._headless)
        try:
            tab = browser.main_tab
            if tab is None:
                raise RuntimeError("zendriver started without a main tab")
            await asyncio.wait_for(
                tab.get(url), timeout=ZENDRIVER_PROBE_TIMEOUT_S
            )
            if settle_seconds > 0:
                await tab.sleep(settle_seconds)
            raw_html = await tab.get_content()
        finally:
            await self._safely_stop(browser)
        return HtmlSnippet.from_raw(url=url, raw_html=raw_html or "")

    @staticmethod
    async def _safely_stop(browser: zd.Browser) -> None:
        """Tear the browser down, never raising over the caller's error."""
        try:
            await browser.stop()
        except Exception:
            logger.exception("failed to stop zendriver browser")
