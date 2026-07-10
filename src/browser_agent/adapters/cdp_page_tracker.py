import asyncio
import time
from collections import deque
from typing import Any

import zendriver as zd

from loguru import logger


class CdpPageTracker:
    """Track a single browser tab's navigation and network activity via CDP events.

    Subscribes to ``Page.frameStoppedLoading``, ``Page.domContentEventFired``,
    ``Network.requestWillBeSent``, ``Network.loadingFinished`` and
    ``Network.loadingFailed`` on a single zendriver tab and exposes
    event-driven waits instead of busy polling.

    ``Page.lifecycleEvent`` is not delivered by this Chrome/zendriver build
    (measured: 0 events), so frame-level events are used as the readiness
    signal instead. Network activity is tracked per ``loader_id`` so the
    in-flight counter (and the ``networkidle`` quiet-window fallback) reflect
    the active navigation.
    """

    def __init__(self, tab: zd.Tab) -> None:
        self._tab = tab
        self._loader_id: str | None = None
        self._lifecycle_events: deque[tuple[str, str, float]] = deque(maxlen=50)
        self._frame_events: deque[tuple[str, float]] = deque(maxlen=50)
        self._in_flight_by_loader: dict[str, set[str]] = {}
        self._all_requests: set[str] = set()
        self._finished_requests: set[str] = set()
        self._navigation_started: bool = False
        self._last_event_signal = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._main_document_status: int | None = None
        self._main_document_mime_type: str | None = None
        self._main_document_final_url: str | None = None
        self._main_document_request_id: str | None = None

    async def attach(self) -> None:
        """Register CDP event handlers and enable their CDP domains.

        ``add_handler`` alone does not enable the ``Page`` / ``Network``
        domains; ``_register_handlers`` does. Both must run before
        navigation, otherwise no events are delivered.
        """
        self._tab.add_handler(zd.cdp.page.FrameStoppedLoading, self._on_frame_stopped)
        self._tab.add_handler(zd.cdp.page.DomContentEventFired, self._on_dom_content_loaded)
        self._tab.add_handler(zd.cdp.network.RequestWillBeSent, self._on_request_will_be_sent)
        self._tab.add_handler(zd.cdp.network.ResponseReceived, self._on_response_received)
        self._tab.add_handler(zd.cdp.network.LoadingFinished, self._on_loading_finished)
        self._tab.add_handler(zd.cdp.network.LoadingFailed, self._on_loading_failed)
        await self._tab._register_handlers()

    def detach(self) -> None:
        """Remove registered CDP handlers from the underlying tab."""
        try:
            self._tab.remove_handlers(zd.cdp.page.FrameStoppedLoading)
            self._tab.remove_handlers(zd.cdp.page.DomContentEventFired)
            self._tab.remove_handlers(zd.cdp.network.RequestWillBeSent)
            self._tab.remove_handlers(zd.cdp.network.ResponseReceived)
            self._tab.remove_handlers(zd.cdp.network.LoadingFinished)
            self._tab.remove_handlers(zd.cdp.network.LoadingFailed)
        except Exception:
            pass

    def begin_navigation(self, loader_id: str | None, clear_main_document_state: bool = True) -> None:
        """Mark a new navigation as active and clear stale per-navigation state.

        Call once before driving the navigation (``loader_id`` may be
        ``None`` if unknown yet) and again with the captured ``loader_id``
        after ``Page.navigate`` returns. Frame events that fire during
        navigation are kept so ``wait_for_lifecycle`` sees them.

        The main-document response fields (status, mime type, final URL,
        request id) are cleared only when ``clear_main_document_state`` is
        ``True``. The post-navigation call must pass ``False`` because the
        ``Network.responseReceived`` event for the main document typically
        fires *during* the navigation call (before ``Page.navigate``
        returns), and clearing those fields afterwards would discard the
        only signal we get to detect a PDF main frame. The in-crawl PDF
        capture path depends on those fields being populated.
        """
        self._loader_id = loader_id
        self._lifecycle_events.clear()
        self._navigation_started = True
        if clear_main_document_state:
            self._main_document_status = None
            self._main_document_mime_type = None
            self._main_document_final_url = None
            self._main_document_request_id = None
        if loader_id is not None and loader_id not in self._in_flight_by_loader:
            self._in_flight_by_loader[loader_id] = set()
        self._last_event_signal.set()

    def reset(self) -> None:
        """Discard all tracked state. Used between tabs in long-lived sessions."""
        self._loader_id = None
        self._lifecycle_events.clear()
        self._frame_events.clear()
        self._navigation_started = False
        self._in_flight_by_loader.clear()
        self._all_requests.clear()
        self._finished_requests.clear()
        self._main_document_status = None
        self._main_document_mime_type = None
        self._main_document_final_url = None
        self._main_document_request_id = None
        self._last_event_signal = asyncio.Event()

    async def wait_for_request_finished(
        self,
        request_id: str,
        timeout: float,
        poll_interval: float = 0.02,
    ) -> bool:
        """Block until ``loadingFinished`` (or ``loadingFailed``) fires for ``request_id``.

        ``Network.getResponseBody`` is officially only valid after the
        request has finished loading; calling it earlier returns
        ``No data found for resource with given identifier``. The
        capture path uses this wait before reading the main-frame body.
        """
        if not request_id:
            return False
        deadline = self._loop.time() + timeout
        while True:
            if request_id in self._finished_requests:
                return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._last_event_signal.clear()
            try:
                await asyncio.wait_for(
                    self._last_event_signal.wait(),
                    timeout=min(remaining, poll_interval),
                )
            except asyncio.TimeoutError:
                pass

    @property
    def main_document_status(self) -> int | None:
        """HTTP status code of the main document response, or ``None`` if not yet received."""
        return self._main_document_status

    @property
    def main_document_mime_type(self) -> str | None:
        """Mime type of the main document response, or ``None`` if not yet received."""
        return self._main_document_mime_type

    @property
    def main_document_final_url(self) -> str | None:
        """Final URL of the main document response after redirects, or ``None``."""
        return self._main_document_final_url

    @property
    def main_document_request_id(self) -> str | None:
        """CDP request id of the main document response, or ``None`` if not yet received."""
        return self._main_document_request_id

    def main_document_is_pdf(self) -> bool:
        """Return ``True`` when the main document response advertises a PDF body."""
        mime = (self._main_document_mime_type or "").lower()
        return "application/pdf" in mime

    async def wait_for_lifecycle(
        self,
        name: str,
        timeout: float,
        poll_interval: float = 0.05,
    ) -> bool:
        """Block until a readiness event fires for the active navigation.

        ``name`` is kept as ``"load"`` / ``"networkIdle"`` for caller
        compatibility, but the underlying signal is now frame-level:
        ``Page.frameStoppedLoading`` (≈ "load") and
        ``Page.domContentEventFired`` (≈ "DOMContentLoaded"). The
        ``Page.lifecycleEvent`` stream is not delivered by this
        Chrome/zendriver build, so it cannot be used.

        Returns ``True`` if the event fired within ``timeout`` seconds,
        ``False`` otherwise.
        """
        deadline = self._loop.time() + timeout
        while True:
            if self._navigation_started and self._frame_events:
                return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._last_event_signal.clear()
            try:
                await asyncio.wait_for(self._last_event_signal.wait(), timeout=min(remaining, poll_interval))
            except asyncio.TimeoutError:
                pass

    async def wait_for_network_idle(
        self,
        quiet_window_ms: int,
        timeout: float,
        poll_interval: float = 0.05,
    ) -> bool:
        """Wait until no requests are in-flight for the active loader for ``quiet_window_ms``.

        Returns ``True`` if the network reached idle within ``timeout`` seconds,
        ``False`` otherwise. ``networkQuietWindowMs`` is honoured by Chrome to
        emit the ``networkIdle`` lifecycle event; this method is the
        fallback for cases where the event is missed.
        """
        quiet_seconds = quiet_window_ms / 1000.0
        deadline = self._loop.time() + timeout
        last_active_at = self._loop.time()
        while True:
            if self._has_pending_requests_for_active_loader():
                last_active_at = self._loop.time()
            else:
                if self._loop.time() - last_active_at >= quiet_seconds:
                    return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._last_event_signal.clear()
            try:
                await asyncio.wait_for(self._last_event_signal.wait(), timeout=min(remaining, poll_interval))
            except asyncio.TimeoutError:
                pass

    def has_pending_requests_for_active_navigation(self) -> bool:
        """Return ``True`` if any request is currently in-flight for the active loader."""
        return self._has_pending_requests_for_active_loader()

    @property
    def loader_id(self) -> str | None:
        return self._loader_id

    @property
    def last_lifecycle_event_name(self) -> str | None:
        if not self._frame_events:
            return None
        return self._frame_events[-1][0]

    def _matches_active_loader(self, loader_id: str) -> bool:
        if self._loader_id is None:
            return True
        return loader_id == self._loader_id

    def _has_pending_requests_for_active_loader(self) -> bool:
        if self._loader_id is None:
            return any(self._in_flight_by_loader.values())
        return bool(self._in_flight_by_loader.get(self._loader_id))

    def _on_frame_stopped(self, event: Any) -> None:
        timestamp = float(getattr(event, "timestamp", time.monotonic()) or time.monotonic())
        self._frame_events.append(("frameStoppedLoading", timestamp))
        self._lifecycle_events.append(("", "load", timestamp))
        self._last_event_signal.set()
        logger.debug("CDP frameStoppedLoading")

    def _on_dom_content_loaded(self, event: Any) -> None:
        timestamp = float(getattr(event, "timestamp", time.monotonic()) or time.monotonic())
        self._frame_events.append(("domContentEventFired", timestamp))
        self._lifecycle_events.append(("", "DOMContentLoaded", timestamp))
        self._last_event_signal.set()
        logger.debug("CDP domContentEventFired")

    def _on_request_will_be_sent(self, event: Any) -> None:
        loader_id = str(getattr(event, "loader_id", "") or "")
        request_id = str(getattr(event, "request_id", "") or "")
        if not request_id:
            return
        self._all_requests.add(request_id)
        bucket = self._in_flight_by_loader.setdefault(loader_id, set())
        bucket.add(request_id)
        self._last_event_signal.set()

    def _on_loading_finished(self, event: Any) -> None:
        request_id = str(getattr(event, "request_id", "") or "")
        if request_id:
            self._finished_requests.add(request_id)
        self._mark_request_done(request_id)

    def _on_loading_failed(self, event: Any) -> None:
        request_id = str(getattr(event, "request_id", "") or "")
        if request_id:
            self._finished_requests.add(request_id)
        # Note: do not call _mark_request_done for failed requests --
        # wait_for_network_idle relies on the in-flight counter to settle
        # only on success.

    def _on_response_received(self, event: Any, _connection: Any = None) -> None:
        """Capture the HTTP status of the main document (ResourceType.DOCUMENT).

        Chrome's built-in PDF viewer loads the PDF as a DOCUMENT response with
        ``application/pdf`` and then opens its own viewer page as a second
        DOCUMENT response with ``text/html`` and a ``chrome-extension://`` URL.
        Without guarding against that second response, the PDF mime type would
        be overwritten and ``main_document_is_pdf`` would return ``False`` —
        silently skipping the in-crawl capture. So non-http(s) DOCUMENT
        responses (the PDF viewer's extension page) are ignored.
        """
        if getattr(event, "type_", None) == zd.cdp.network.ResourceType.DOCUMENT:
            response_url = getattr(event.response, "url", None) or ""
            if response_url.startswith("http://") or response_url.startswith("https://"):
                self._main_document_status = event.response.status
                self._main_document_mime_type = getattr(event.response, "mimeType", None) or getattr(
                    event.response, "mime_type", None
                )
                self._main_document_final_url = response_url
                self._main_document_request_id = str(getattr(event, "request_id", "") or "") or None
        request_id = str(getattr(event, "request_id", "") or "")
        self._mark_request_done(request_id)
        self._last_event_signal.set()

    def _mark_request_done(self, request_id: str) -> None:
        for bucket in self._in_flight_by_loader.values():
            bucket.discard(request_id)
        self._last_event_signal.set()
