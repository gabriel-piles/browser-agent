import asyncio
from typing import Any

import zendriver as zd

from loguru import logger

from browser_agent.adapters.cdp_page_tracker import CdpPageTracker
from browser_agent.configuration import (
    ANCHOR_STABILITY_MAX_WAIT_SECONDS,
    ANCHOR_STABILITY_MIN_WAIT_SECONDS,
    ANCHOR_STABILITY_POLL_INTERVAL_SECONDS,
    ANCHOR_STABILITY_REQUIRED_STABLE_POLLS,
    PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
    PAGE_LOAD_TIMEOUT_SECONDS,
)

WaitStrategy = str  # "load" | "networkidle"


class ZendriverPageWait:
    """High-level wait helpers that combine CDP frame events with DOM checks.

    Wraps a freshly opened zendriver ``Tab`` together with its
    :class:`CdpPageTracker`. Two responsibilities:

    1. ``wait_until_ready`` waits for either the ``load`` or the
       ``networkIdle`` readiness signal for the active navigation. ``load``
       maps to ``Page.frameStoppedLoading``; ``networkIdle`` additionally
       checks the in-flight request counter.
    2. ``wait_for_anchor_stability`` polls the anchor + iframe signature
       and returns once it is stable, non-empty and ``min_wait`` has
       elapsed, bounded by ``ANCHOR_STABILITY_MAX_WAIT_SECONDS``.
    """

    def __init__(
        self,
        tab: zd.Tab,
        tracker: CdpPageTracker,
        default_strategy: WaitStrategy = "networkidle",
        default_load_timeout: float = PAGE_LOAD_TIMEOUT_SECONDS,
        quiet_window_ms: int = PAGE_LOAD_NETWORK_QUIET_WINDOW_MS,
    ) -> None:
        self._tab = tab
        self._tracker = tracker
        self._default_strategy = default_strategy
        self._default_load_timeout = default_load_timeout
        self._quiet_window_ms = quiet_window_ms

    async def wait_until_ready(
        self,
        strategy: WaitStrategy | None = None,
        timeout: float | None = None,
    ) -> None:
        chosen = strategy or self._default_strategy
        budget = timeout if timeout is not None else self._default_load_timeout
        if chosen == "load":
            ok = await self._tracker.wait_for_lifecycle("load", timeout=budget)
            if not ok:
                logger.warning(
                    "load lifecycle event did not fire within {:.1f}s for {}",
                    budget,
                    getattr(self._tab, "url", "<unknown>"),
                )
            return
        if chosen == "networkidle":
            idle = await self._tracker.wait_for_lifecycle("networkIdle", timeout=budget)
            if idle:
                return
            idle = await self._tracker.wait_for_network_idle(
                quiet_window_ms=self._quiet_window_ms,
                timeout=max(0.5, budget * 0.25),
            )
            if idle:
                return
            load_ok = await self._tracker.wait_for_lifecycle(
                "load",
                timeout=max(0.5, budget * 0.25),
            )
            if load_ok:
                return
            logger.warning(
                "network did not reach idle within {:.1f}s for {}",
                budget,
                getattr(self._tab, "url", "<unknown>"),
            )
            return
        raise ValueError(f"Unsupported wait_until strategy: {chosen!r}")

    async def wait_for_anchor_stability(
        self,
        min_wait: float = ANCHOR_STABILITY_MIN_WAIT_SECONDS,
        max_wait: float = ANCHOR_STABILITY_MAX_WAIT_SECONDS,
        poll_interval: float = ANCHOR_STABILITY_POLL_INTERVAL_SECONDS,
        required_stable_polls: int = ANCHOR_STABILITY_REQUIRED_STABLE_POLLS,
    ) -> tuple[list[Any], list[Any]]:
        """Return ``(anchors, iframes)`` once the rendered link set has stabilised.

        Polls the page's anchor + iframe signature until it stays unchanged
        for ``required_stable_polls`` consecutive polls and ``min_wait`` has
        elapsed. ``max_wait`` is the hard ceiling. Returns JSON-deserialised
        dicts (not zendriver ``ElementHandle`` objects) for each anchor / iframe.
        """
        elapsed = 0.0
        prev_signature: tuple | None = None
        stable_polls = 0
        min_wait_deadline = min_wait
        while elapsed < max_wait:
            anchors, iframes, signature = await self._snapshot_links()
            if signature == prev_signature and signature:
                stable_polls += 1
            else:
                stable_polls = 0
            prev_signature = signature
            if stable_polls >= required_stable_polls and elapsed >= min_wait_deadline and signature:
                return anchors, iframes
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    async def _snapshot_links(self) -> tuple[list[Any], list[Any], tuple]:
        """Extract anchors, iframes and their signature via one ``evaluate``.

        Returns ``(anchors, iframes, signature)`` where anchors and iframes
        are lists of dicts with the attributes the caller needs (href, text,
        title, id, class, rel, target for anchors; src for iframes).
        """
        import json

        js = (
            "(() => {"
            "  const as = [...document.querySelectorAll('a')];"
            "  const ifs = [...document.querySelectorAll('iframe[src]')];"
            "  return JSON.stringify({"
            "    a: as.map(x => ({"
            "      href: x.href || x.getAttribute('href') || '',"
            "      text: (x.textContent || '').trim(),"
            "      title: x.getAttribute('title') || '',"
            "      id: x.id || '',"
            "      class: x.className || '',"
            "      rel: x.getAttribute('rel') || '',"
            "      target: x.getAttribute('target') || ''"
            "    })),"
            "    i: ifs.map(x => ({ src: x.src || x.getAttribute('src') || '' }))"
            "  });"
            "})()"
        )
        try:
            raw = await asyncio.wait_for(self._tab.evaluate(js), timeout=3.0)
            data = json.loads(raw) if raw else {"a": [], "i": []}
        except Exception:
            return [], [], ()
        anchors = data.get("a", [])
        iframes = data.get("i", [])
        signature = tuple(a.get("href") or "" for a in anchors) + tuple(i.get("src") or "" for i in iframes)
        return anchors, iframes, signature
