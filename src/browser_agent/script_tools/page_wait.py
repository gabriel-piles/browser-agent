"""Minimal CDP tracker for emitted scripts.

Moved verbatim from ``browser_agent.adapters.emitted_page_wait``.
Mirrors the relevant subset of ``browser_agent.adapters.cdp_page_tracker``
so the helper works the same way the persistent session does. One
instance is cached on the tab for the script's lifetime.
"""

from __future__ import annotations

import asyncio
import time

import zendriver as zd

_PAGE_WAIT_QUIET_WINDOW_MS = 500
_PAGE_WAIT_DEFAULT_TIMEOUT_S = 30.0
_ANCHOR_DEFAULT_TIMEOUT_S = 8.0
_ANCHOR_POLL_INTERVAL_S = 0.2
_ANCHOR_REQUIRED_STABLE_POLLS = 2


class _EmittedPageWaitTracker:
    """Minimal CDP tracker for emitted scripts.

    Mirrors the relevant subset of ``browser_agent.adapters.cdp_page_tracker``
    so the helper works the same way the persistent session does. One
    instance is cached on the tab for the script's lifetime.
    """

    def __init__(self, tab):
        self._tab = tab
        self._loop = asyncio.get_running_loop()
        self._frame_events = []
        self._in_flight = set()
        self._all_requests = set()
        self._loader_id = None
        self._navigation_started = False
        self._frame_watermark = 0
        self._signal = asyncio.Event()

    async def attach(self):
        # Register event handlers BEFORE sending domain enable commands,
        # so the internal ``_register_handlers()`` call (triggered by
        # ``send()``) sees the handlers and wires the CDP dispatch
        # correctly.  Matches the order in
        # ``CdpPageTracker.attach()``.
        self._tab.add_handler(zd.cdp.page.FrameStoppedLoading, self._on_frame_stopped)
        self._tab.add_handler(zd.cdp.network.RequestWillBeSent, self._on_request_will_be_sent)
        self._tab.add_handler(zd.cdp.network.LoadingFinished, self._on_loading_finished)
        self._tab.add_handler(zd.cdp.network.LoadingFailed, self._on_loading_failed)
        await self._tab.send(zd.cdp.page.enable())
        await self._tab.send(zd.cdp.network.enable())

    def begin_navigation(self, loader_id):
        # ``begin_navigation`` is called from the helper AFTER
        # ``tab.get`` has already issued the navigation. The
        # ``frameStoppedLoading`` event for that navigation can fire
        # either BEFORE or AFTER this call (Chrome may flush it on the
        # CDP connection before we get back control). The helper must
        # accept both orderings:
        #
        # 1. If at call time the in-flight request count is zero AND
        #    the most recent frame event is recent (<= 250 ms), the
        #    page is considered already loaded for this navigation —
        #    ``wait_for_frame_stopped`` returns immediately.
        # 2. Otherwise we record a watermark and wait for a frame event
        #    that arrives strictly after it.
        # We do NOT clear ``_frame_events``: the watermark tracks only
        # "events newer than the last seen state", which is robust
        # against both orderings and against stale events from prior
        # navigations that we have not yet observed.
        self._loader_id = loader_id or self._loader_id
        self._frame_watermark = len(self._frame_events)
        self._navigation_started = True
        self._signal.set()

    def has_pending(self):
        return bool(self._in_flight)

    async def _check_ready_state(self, expected_url):
        # ``document.readyState == "complete"`` is the strongest signal
        # that the page is fully loaded regardless of whether Chrome
        # fired a fresh ``frameStoppedLoading`` (same-URL reload,
        # BFCache hits, missed events from a late-attached tracker,
        # etc.). The check is independent of the in-flight request
        # count because ``readyState`` flips to ``complete`` only after
        # every sub-resource has loaded.
        #
        # When ``expected_url`` is None (caller omitted the URL) we
        # still poll ``readyState`` against the tab's current URL —
        # this is the safety net for scripts that forget to call
        # ``prepare_page_wait`` before the first navigation, since the
        # tracker then attaches AFTER ``frameStoppedLoading`` already
        # fired and would otherwise wait forever.
        if not self._navigation_started:
            return None
        try:
            cur_url = self._tab.url
        except Exception:
            return None
        if expected_url and cur_url != expected_url:
            return None
        try:
            ready = await self._tab.evaluate("document.readyState")
        except Exception:
            return None
        return ready == "complete"

    async def wait_for_frame_stopped(self, timeout, expected_url=None):
        # Fast path 1: a frame event arrived in the last 250 ms AND no
        # requests are currently in-flight — page is ready.
        now = time.monotonic()
        if (
            self._navigation_started
            and self._frame_events
            and not self._in_flight
            and (now - self._frame_events[-1]) <= 0.25
        ):
            return True
        # Fast path 2: ``document.readyState`` is ``complete``. Works
        # for same-URL reloads and for the case where the tracker
        # attached after the navigation already finished.
        if await self._check_ready_state(expected_url):
            return True
        deadline = self._loop.time() + timeout
        while True:
            if self._navigation_started and len(self._frame_events) > self._frame_watermark:
                return True
            if await self._check_ready_state(expected_url):
                return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._signal.clear()
            try:
                await asyncio.wait_for(self._signal.wait(), timeout=min(remaining, 0.05))
            except asyncio.TimeoutError:
                pass

    async def wait_for_network_quiet(self, quiet_window_ms, timeout):
        quiet_seconds = quiet_window_ms / 1000.0
        deadline = self._loop.time() + timeout
        last_active = self._loop.time()
        while True:
            if self._in_flight:
                last_active = self._loop.time()
            else:
                if self._loop.time() - last_active >= quiet_seconds:
                    return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._signal.clear()
            try:
                await asyncio.wait_for(self._signal.wait(), timeout=min(remaining, 0.05))
            except asyncio.TimeoutError:
                pass

    def _on_frame_stopped(self, _event):
        self._frame_events.append(time.monotonic())
        self._signal.set()

    def _on_request_will_be_sent(self, event):
        request_id = str(getattr(event, "request_id", "") or "")
        if request_id:
            self._in_flight.add(request_id)
            self._all_requests.add(request_id)
        self._signal.set()

    def _on_loading_finished(self, event):
        request_id = str(getattr(event, "request_id", "") or "")
        if request_id:
            self._in_flight.discard(request_id)
        self._signal.set()

    def _on_loading_failed(self, event):
        request_id = str(getattr(event, "request_id", "") or "")
        if request_id:
            self._in_flight.discard(request_id)
        self._signal.set()


async def _get_tracker(tab):
    tracker = getattr(tab, "_emitted_wait_tracker", None)
    if tracker is None:
        tracker = _EmittedPageWaitTracker(tab)
        await tracker.attach()
        tab._emitted_wait_tracker = tracker
    return tracker


async def prepare_page_wait(tab):
    """Attach the CDP tracker to ``tab`` BEFORE the first navigation.

    Call once at the top of ``main`` — before any ``tab.get(url)`` — so
    the tracker receives the ``Page.frameStoppedLoading`` and
    ``Network.*`` events for the first navigation. If the tracker is not
    pre-attached, the first ``wait_for_page_ready`` call will miss the
    events of the navigation that is already in flight.
    """
    await _get_tracker(tab)


async def wait_for_page_ready(
    tab, url=None, timeout=_PAGE_WAIT_DEFAULT_TIMEOUT_S, quiet_window_ms=_PAGE_WAIT_QUIET_WINDOW_MS
):
    """Block until the active navigation has loaded and the network is idle.

    Drop-in replacement for ``await tab.sleep(...)`` after ``tab.get(url)``.
    Awaits ``Page.frameStoppedLoading`` (or, for same-URL reloads, polls
    ``document.readyState``) then waits for the in-flight request counter
    to be quiet for ``quiet_window_ms``. Returns silently on success;
    raises ``TimeoutError`` only if the entire ``timeout`` budget is
    consumed by the frame-stopped wait (network-idle is a best-effort
    secondary check that never raises).

    ``url`` (optional) is the URL the caller navigated to. When set, the
    helper also accepts the same-URL-reload case where Chrome does not
    fire a fresh ``frameStoppedLoading``.
    """
    tracker = await _get_tracker(tab)
    tracker.begin_navigation(None)
    frame_budget = max(1.0, timeout * 0.75)
    if not await tracker.wait_for_frame_stopped(frame_budget, expected_url=url):
        raise TimeoutError(f"frame did not stop loading within {frame_budget:.1f}s")
    quiet_budget = max(0.5, timeout - frame_budget)
    await tracker.wait_for_network_quiet(quiet_window_ms, quiet_budget)


async def wait_for_anchors(
    tab,
    selector,
    timeout=_ANCHOR_DEFAULT_TIMEOUT_S,
    poll_interval=_ANCHOR_POLL_INTERVAL_S,
    required_polls=_ANCHOR_REQUIRED_STABLE_POLLS,
):
    """Block until ``selector`` matches at least one non-empty element.

    Drop-in replacement for ``await tab.sleep(...)`` before reading
    elements populated by a filter click or XHR. Polls the selector
    every ``poll_interval`` seconds; returns once the match count is
    non-zero for ``required_polls`` consecutive polls OR ``timeout``
    elapses. Returns ``(matched_count, sample_text)`` so the caller can
    log what it found; raises ``TimeoutError`` when the timeout elapses
    with zero matches so the script fails loudly instead of silently
    producing an empty result set.
    """
    deadline = time.monotonic() + timeout
    stable = 0
    last_count = 0
    while True:
        try:
            result = await tab.evaluate(f"document.querySelectorAll({selector!r}).length")
            count = int(result) if result is not None else 0
        except Exception:
            count = 0
        if count > 0:
            stable += 1
            if stable >= required_polls:
                try:
                    sample = await tab.evaluate(f"(document.querySelector({selector!r}) || {{}}).textContent || ''")
                except Exception:
                    sample = ""
                return count, (sample or "").strip()[:200]
            last_count = count
        else:
            stable = 0
        if time.monotonic() >= deadline:
            raise TimeoutError(f"selector {selector!r} matched 0 elements after {timeout:.1f}s")
        await asyncio.sleep(poll_interval)


_NAVIGATE_TIMEOUT_S = 45.0
_TITLE_PROBE_TIMEOUT_S = 10.0


async def goto_ready(tab, url, timeout=6.0, quiet_window_ms=300) -> None:
    """Navigate to ``url`` and wait for render, tolerating a Cloudflare challenge.

    ``await tab.get(url)``, then poll ``document.title`` up to 3 times for
    "Just a moment" / "Attention Required" (waiting 10s between polls), then
    wait for the page to be ready (best-effort, never raises). Finally a
    short settle sleep.
    """
    try:
        await asyncio.wait_for(tab.get(url), timeout=_NAVIGATE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise TimeoutError(f"goto_ready: navigation to {url} did not finish in {_NAVIGATE_TIMEOUT_S}s")
    for _ in range(3):
        try:
            title = await asyncio.wait_for(tab.evaluate("document.title"), timeout=_TITLE_PROBE_TIMEOUT_S) or ""
        except asyncio.TimeoutError:
            break
        if "just a moment" not in title.lower() and "attention required" not in title.lower():
            break
        await tab.sleep(10)
    try:
        await asyncio.wait_for(
            wait_for_page_ready(tab, url, timeout=timeout, quiet_window_ms=quiet_window_ms),
            timeout=timeout + 2,
        )
    except Exception:
        pass
    await tab.sleep(0.4)


_CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verify you are human",
    "human verification",
    "are you human",
    "security check",
    "captcha",
)


async def is_challenge(tab) -> bool:
    """Return True when the current page is an anti-bot challenge page."""
    try:
        title = str(await tab.evaluate("document.title") or "").lower()
    except Exception:
        return False


async def wait_for_challenge_clear(tab, max_wait: float = 45.0, poll_interval: float = 5.0) -> bool:
    """Poll until the challenge clears; True when clear, False on timeout."""
    waited = 0.0
    while waited < max_wait:
        if not await is_challenge(tab):
            return True
        await tab.sleep(poll_interval)
        waited += poll_interval

    return not await is_challenge(tab)
