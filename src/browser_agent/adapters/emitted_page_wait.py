"""Self-contained page-wait helper inlined into every emitted script.

The generated scripts (validation runs in ``SubprocessScriptRunnerAdapter``
and the final script written to ``data/scripts/``) are self-contained by
contract — they MUST NOT import from this project. They need the same
page-load readiness signal the persistent :class:`ZendriverBrowserSession`
gives the agent (CDP ``Page.frameStoppedLoading`` + network-idle), so the
helper is shipped as a plain-Python string and prepended to every emitted
``python_code``.

Three call sites:

* :func:`prepare_page_wait` — call once at the top of ``main`` BEFORE the
  first ``tab.get(url)`` so the tracker receives the
  ``frameStoppedLoading`` and ``Network.*`` events for the first
  navigation. If the tracker is not pre-attached, the first
  ``wait_for_page_ready`` call will miss the events of the navigation
  that is already in flight.
* :func:`wait_for_page_ready` — drop in for ``tab.sleep(...)`` after
  every ``tab.get(url)``. Waits for the active navigation's frame to
  stop loading AND for the network to be quiet for ``~500ms``. Pass
  the URL you navigated to so the helper can handle same-URL reloads
  (where Chrome does not fire a fresh ``frameStoppedLoading``).
* :func:`wait_for_anchors` — drop in for ``tab.sleep(...)`` before
  reading elements populated by a filter / XHR. Polls a CSS selector
  until it matches at least one element for ``required_polls`` consecutive
  polls, bounded by ``timeout`` seconds.

The helper caches a tracker on the tab (``tab._emitted_wait_tracker``) so
subsequent navigations on the same tab reuse the same CDP subscriptions
instead of re-enabling the ``Page`` and ``Network`` domains.

Update the string in lockstep with the standalone tracker if the CDP
contract changes. The two implementations must stay aligned so validation
behaviour matches the live browser session.
"""

from __future__ import annotations


def with_emitted_page_wait(python_code: str) -> str:
    """Prepend the vendored page-wait helper to ``python_code``.

    Both the validation runner (``SubprocessScriptRunnerAdapter``) and the
    final-script emit path (``generate_script._emit``) call this so the
    helper appears at the top of every script the user or the validator
    runs. The helper is idempotent: if the script already contains the
    block marker it is returned unchanged.
    """
    if "BEGIN emitted page-wait helper" in python_code:
        return python_code
    return f"{EMITTED_PAGE_WAIT_BLOCK}{python_code}"


# This block is intentionally a single literal string. The
# ``subprocess_script_runner_adapter`` and the ``generate_script`` driver
# concatenate it in front of the LLM's emitted code so the script gets a
# real page-load signal without importing from this project.
EMITTED_PAGE_WAIT_BLOCK = '''\
# ── BEGIN emitted page-wait helper (vendored from browser_agent) ──
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
        await self._tab.send(zd.cdp.page.enable())
        await self._tab.send(zd.cdp.network.enable())
        self._tab.add_handler(zd.cdp.page.FrameStoppedLoading, self._on_frame_stopped)
        self._tab.add_handler(zd.cdp.network.RequestWillBeSent, self._on_request_will_be_sent)
        self._tab.add_handler(zd.cdp.network.LoadingFinished, self._on_loading_finished)
        self._tab.add_handler(zd.cdp.network.LoadingFailed, self._on_loading_failed)

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
        # ``document.readyState == \"complete\"`` is the strongest signal
        # that the page is fully loaded regardless of whether Chrome
        # fired a fresh ``frameStoppedLoading`` (same-URL reload,
        # BFCache hits, etc.). The check is independent of the
        # in-flight request count because ``readyState`` flips to
        # ``complete`` only after every sub-resource has loaded.
        if not expected_url or not self._navigation_started:
            return None
        try:
            cur_url = self._tab.url
        except Exception:
            return None
        if cur_url != expected_url:
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
        # Fast path 2: ``document.readyState`` is ``complete`` for the
        # expected URL. Works for same-URL reloads where Chrome does
        # not fire a fresh ``frameStoppedLoading``.
        if expected_url:
            if await self._check_ready_state(expected_url):
                return True
        deadline = self._loop.time() + timeout
        while True:
            if self._navigation_started and len(self._frame_events) > self._frame_watermark:
                return True
            if expected_url:
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


async def wait_for_page_ready(tab, url=None, timeout=_PAGE_WAIT_DEFAULT_TIMEOUT_S,
                              quiet_window_ms=_PAGE_WAIT_QUIET_WINDOW_MS):
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
        raise TimeoutError(
            f"frame did not stop loading within {frame_budget:.1f}s"
        )
    quiet_budget = max(0.5, timeout - frame_budget)
    await tracker.wait_for_network_quiet(quiet_window_ms, quiet_budget)


async def wait_for_anchors(tab, selector, timeout=_ANCHOR_DEFAULT_TIMEOUT_S,
                           poll_interval=_ANCHOR_POLL_INTERVAL_S,
                           required_polls=_ANCHOR_REQUIRED_STABLE_POLLS):
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
            result = await tab.evaluate(
                f"document.querySelectorAll({selector!r}).length"
            )
            count = int(result) if result is not None else 0
        except Exception:
            count = 0
        if count > 0:
            stable += 1
            if stable >= required_polls:
                try:
                    sample = await tab.evaluate(
                        f"(document.querySelector({selector!r})"
                        f" || {{}}).textContent || ''"
                    )
                except Exception:
                    sample = ""
                return count, (sample or "").strip()[:200]
            last_count = count
        else:
            stable = 0
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"selector {selector!r} matched 0 elements after {timeout:.1f}s"
            )
        await asyncio.sleep(poll_interval)
# ── END emitted page-wait helper ──
'''
