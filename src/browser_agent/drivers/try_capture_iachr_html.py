"""Standalone zendriver script: capture ALL lazy-loaded HTML from the IACHR country-reports page.

Vendors the same clean-launch, page-wait and save-page-html helpers the
step-0 agent emits, then drives the page with an aggressive scroll +
virtualized-list capture so every report card (even ones the page only
mounts while they are on-screen) ends up in the saved HTML file.

Run:
    python -m browser_agent.drivers.try_capture_iachr_html

The captured HTML is written to ``data/runs/corteidh_country_reports/downloads/``
next to the step-0 output so the two are comparable.
"""

from __future__ import annotations

import asyncio
import hashlib
import os as _os
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import zendriver as zd

# ── constants ──────────────────────────────────────────────────────
TARGET_URL = "https://www.oas.org/en/iachr/jsForm/?File=/en/iachr/reports/country.asp"
OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "runs" / "corteidh_country_reports" / "downloads"
PROFILE_DIR = Path(__file__).resolve().parents[3] / "data" / "runs" / "corteidh_country_reports" / "profile"
HTML_FILENAME = "iachr_country_full.html"
CARD_SELECTOR = "#allReports > div.srFlyTop, #allReports > div.srFlyBottom"
SCROLL_PAUSE_S = 0.6
SCROLL_MAX_ITERS = 400
STABLE_BREAK = 5

_EMITTED_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
_CHROMIUM_BIN = "/usr/bin/chromium"
_REAL_CHROMIUM_PROFILE = Path.home() / ".config" / "chromium"

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = {runtime: {}};
if (window.CDC_adoQpoasnfa76pfcZLmcfl_Promise) {
    window.CDC_adoQpoasnfa76pfcZLmcfl_Promise = undefined;
}
if (window.cdc_adoQpoasnfa76pfcZLmcfl_Promise) {
    window.cdc_adoQpoasnfa76pfcZLmcfl_Promise = undefined;
}
Object.defineProperty(window, 'outerWidth', {get: () => window.innerWidth});
Object.defineProperty(window, 'outerHeight', {get: () => window.innerHeight});
"""

_PAGE_WAIT_QUIET_WINDOW_MS = 500
_PAGE_WAIT_DEFAULT_TIMEOUT_S = 60.0

_SNAPSHOT_JS = """\
    (() => {
        window.__htmlCaptures = window.__htmlCaptures || [];
        window.__htmlCaptures.push(document.documentElement.outerHTML);
        return window.__htmlCaptures.length;
    })()"""

_CONSOLIDATE_JS = """\
    (() => {{
        const caps = window.__htmlCaptures || [];
        if (caps.length === 0) return '';
        const sel = {selector_literal};
        const base = new DOMParser().parseFromString(caps[caps.length - 1], 'text/html');
        const firstCard = base.querySelector(sel);
        const baseContainer = firstCard ? firstCard.parentElement : base.body;
        const seen = new Set();
        for (const c of [...baseContainer.querySelectorAll(sel)]) seen.add(c.outerHTML);
        for (let i = caps.length - 2; i >= 0; i--) {{
            const doc = new DOMParser().parseFromString(caps[i], 'text/html');
            const fc = doc.querySelector(sel);
            if (!fc) continue;
            const container = fc.parentElement || doc.body;
            for (const c of [...container.querySelectorAll(sel)]) {{
                if (!seen.has(c.outerHTML)) {{
                    seen.add(c.outerHTML);
                    baseContainer.insertAdjacentHTML('afterbegin', c.outerHTML);
                }}
            }}
        }}
        for (const e of base.querySelectorAll('script, style, noscript, template, svg')) e.remove();
        return '<!DOCTYPE html>\\n' + base.documentElement.outerHTML;
    }})()"""


# ── clean-launch helper (vendored) ────────────────────────────────
def _free_port() -> int:
    """Bind a free TCP port and return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _seed_profile_if_empty(profile_dir: Path) -> None:
    """Seed an empty profile dir with the real Chromium profile cookies."""
    default_dir = Path(profile_dir) / "Default"
    if (default_dir / "Cookies").exists():
        return
    real_profile = _REAL_CHROMIUM_PROFILE
    if not (real_profile / "Default" / "Cookies").exists():
        alt_profile = Path.home() / ".config" / "google-chrome"
        if (alt_profile / "Default" / "Cookies").exists():
            real_profile = alt_profile
        else:
            return
    shutil.copytree(real_profile, profile_dir, dirs_exist_ok=True, symlinks=True)


async def start_browser(headless=None, user_data_dir=None):
    """Launch clean Chromium and connect zendriver (mirrors the emitted helper)."""
    if headless is None:
        headless = _EMITTED_HEADLESS
    port = _free_port()
    owns_profile = user_data_dir is None
    profile = user_data_dir or tempfile.mkdtemp(prefix="zd_capture_")
    Path(profile).mkdir(parents=True, exist_ok=True)
    _seed_profile_if_empty(profile)
    args = [_CHROMIUM_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={profile}"]
    if headless:
        args.append("--headless=new")
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = await zd.start(host="127.0.0.1", port=port)
    tab = browser.main_tab
    await tab.send(zd.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))
    _original_stop = browser.stop

    async def _clean_stop() -> None:
        await _original_stop()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        if owns_profile:
            shutil.rmtree(profile, ignore_errors=True)

    browser.stop = _clean_stop
    return browser


# ── page-wait tracker (vendored, trimmed) ─────────────────────────
class _PageWaitTracker:
    """Minimal CDP tracker: frame-stopped + in-flight request accounting."""

    def __init__(self, tab) -> None:
        self._tab = tab
        self._loop = asyncio.get_running_loop()
        self._frame_events: list[float] = []
        self._in_flight: set[str] = set()
        self._navigation_started = False
        self._frame_watermark = 0
        self._signal = asyncio.Event()

    async def attach(self) -> None:
        """Register CDP handlers and enable the domains."""
        self._tab.add_handler(zd.cdp.page.FrameStoppedLoading, self._on_frame_stopped)
        self._tab.add_handler(zd.cdp.network.RequestWillBeSent, self._on_request_will_be_sent)
        self._tab.add_handler(zd.cdp.network.LoadingFinished, self._on_loading_finished)
        self._tab.add_handler(zd.cdp.network.LoadingFailed, self._on_loading_failed)
        await self._tab.send(zd.cdp.page.enable())
        await self._tab.send(zd.cdp.network.enable())

    def begin_navigation(self, loader_id=None) -> None:
        """Mark a navigation as started; set the frame watermark."""
        self._frame_watermark = len(self._frame_events)
        self._navigation_started = True
        self._signal.set()

    async def _check_ready(self, expected_url) -> bool | None:
        """Return True when document.readyState == 'complete' for the expected URL."""
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

    async def wait_for_frame_stopped(self, timeout, expected_url=None) -> bool:
        """Block until a frame-stopped event arrives past the watermark."""
        if await self._check_ready(expected_url):
            return True
        deadline = self._loop.time() + timeout
        while True:
            if self._navigation_started and len(self._frame_events) > self._frame_watermark:
                return True
            if await self._check_ready(expected_url):
                return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._signal.clear()
            try:
                await asyncio.wait_for(self._signal.wait(), timeout=min(remaining, 0.05))
            except asyncio.TimeoutError:
                pass

    async def wait_for_network_quiet(self, quiet_window_ms, timeout) -> bool:
        """Block until no in-flight requests persist for ``quiet_window_ms``."""
        quiet_seconds = quiet_window_ms / 1000.0
        deadline = self._loop.time() + timeout
        last_active = self._loop.time()
        while True:
            if self._in_flight:
                last_active = self._loop.time()
            elif self._loop.time() - last_active >= quiet_seconds:
                return True
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return False
            self._signal.clear()
            try:
                await asyncio.wait_for(self._signal.wait(), timeout=min(remaining, 0.05))
            except asyncio.TimeoutError:
                pass

    def _on_frame_stopped(self, _event) -> None:
        self._frame_events.append(time.monotonic())
        self._signal.set()

    def _on_request_will_be_sent(self, event) -> None:
        rid = str(getattr(event, "request_id", "") or "")
        if rid:
            self._in_flight.add(rid)
        self._signal.set()

    def _on_loading_finished(self, event) -> None:
        rid = str(getattr(event, "request_id", "") or "")
        if rid:
            self._in_flight.discard(rid)
        self._signal.set()

    def _on_loading_failed(self, event) -> None:
        rid = str(getattr(event, "request_id", "") or "")
        if rid:
            self._in_flight.discard(rid)
        self._signal.set()


async def _get_tracker(tab) -> _PageWaitTracker:
    """Return (and lazily attach) the cached tracker on the tab."""
    tracker = getattr(tab, "_wait_tracker", None)
    if tracker is None:
        tracker = _PageWaitTracker(tab)
        await tracker.attach()
        tab._wait_tracker = tracker
    return tracker


async def prepare_page_wait(tab) -> None:
    """Attach the CDP tracker before the first navigation."""
    await _get_tracker(tab)


async def wait_for_page_ready(
    tab, url=None, timeout=_PAGE_WAIT_DEFAULT_TIMEOUT_S, quiet_window_ms=_PAGE_WAIT_QUIET_WINDOW_MS
) -> None:
    """Block until navigation loaded and network is quiet."""
    tracker = await _get_tracker(tab)
    tracker.begin_navigation(None)
    frame_budget = max(1.0, timeout * 0.75)
    if not await tracker.wait_for_frame_stopped(frame_budget, expected_url=url):
        raise TimeoutError(f"frame did not stop loading within {frame_budget:.1f}s")
    quiet_budget = max(0.5, timeout - frame_budget)
    await tracker.wait_for_network_quiet(quiet_window_ms, quiet_budget)


# ── atomic write ──────────────────────────────────────────────────
def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp + rename)."""
    part = path.with_name(path.name + ".part")
    try:
        if part.exists():
            part.unlink()
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(part, path)
    except Exception:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        raise


# ── aggressive scroll + virtualized capture ──────────────────────
async def _capture_simple_html(tab) -> str:
    """Single-shot DOM capture via get_content with evaluate fallback."""
    try:
        return await tab.get_content()
    except Exception:
        return await tab.evaluate("document.documentElement.outerHTML")


async def _count_cards(tab) -> int:
    """Return the current number of mounted report cards."""
    try:
        result = await tab.evaluate(f"document.querySelectorAll({CARD_SELECTOR!r}).length")
        return int(result) if result is not None else 0
    except Exception:
        return 0


async def _click_load_more_if_present(tab) -> bool:
    """Click any visible 'load more' / pager control; return True if clicked."""
    clicked = await tab.evaluate(
        """(() => {
            const candidates = [
                'a[onclick*="loadMore"]', 'a[onclick*="LoadMore"]',
                'button.load-more', 'a.load-more',
                '.pagination a:not([disabled])', 'a.next', 'a[rel="next"]',
                'a[onclick*="showMore"]', 'a[onclick*="ShowMore"]',
                '#loadMore', '.btn-load-more',
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) { el.click(); return true; }
            }
            return false;
        })()"""
    )
    return bool(clicked)


async def _scroll_and_capture(tab) -> str:
    """Scroll top-to-bottom (triggering any lazy loads), then single-shot capture.

    The IACHR country-reports page mounts all report cards in the DOM
    at once — there is no inner scroll container and no virtualized
    list (verified live: 49 srFlyTop + 49 srFlyBottom = 98 cards,
    ``#allReports`` scrollHeight ≈ clientHeight). So a plain
    ``get_content()`` after a token scroll captures the complete page.
    The virtualized per-viewport consolidation is NOT used here: it
    picks the last snapshot as base and its dedup logic drops the
    ``srFlyTop`` siblings, losing half the cards.
    """
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.4)
    prev_height = -1
    stable = 0
    for i in range(SCROLL_MAX_ITERS):
        height = await tab.evaluate("document.body.scrollHeight")
        height = int(height) if height is not None else 0
        cards = await _count_cards(tab)
        at_bottom = await tab.evaluate("(window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2")
        if height == prev_height:
            stable += 1
        else:
            stable = 0
        print(f"  iter {i + 1}: height={height} cards={cards} at_bottom={at_bottom} stable={stable}")
        if await _click_load_more_if_present(tab):
            print("    clicked load-more / pager")
            await tab.sleep(0.8)
            stable = 0
        if (at_bottom and stable >= STABLE_BREAK) or stable >= STABLE_BREAK + 2:
            break
        prev_height = height
        await tab.evaluate("window.scrollBy(0, window.innerHeight)")
        await tab.sleep(SCROLL_PAUSE_S)
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.3)
    # Force every report card fully visible before capture. The page
    # uses scrollreveal.min.js, which leaves off-screen cards with
    # inline ``opacity:0`` + a 3D ``transform`` until they are scrolled
    # into view. When the saved HTML is opened locally, the reveal
    # only runs for the first viewport, so the older (pre-1993) cards
    # stay invisible even though they are in the DOM. Removing the
    # scrollreveal inline style on every card makes the saved file
    # render all 98 cards regardless of scroll position.
    await tab.evaluate(
        "document.querySelectorAll('#allReports > div').forEach(c => { c.style.opacity = ''; c.style.transform = ''; c.style.visibility = ''; })"
    )
    return await _capture_simple_html(tab)


# ── main ──────────────────────────────────────────────────────────
async def main() -> None:
    """Launch browser, navigate, scroll-load, capture full HTML, write to disk."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {OUT_DIR}")
    browser = await start_browser(user_data_dir=str(PROFILE_DIR), headless=False)
    tab = browser.main_tab
    try:
        await prepare_page_wait(tab)
        print("Navigating to target URL...")
        await tab.get(TARGET_URL)
        await wait_for_page_ready(tab, url=TARGET_URL)
        await tab.sleep(3)
        print(f"Initial card count: {await _count_cards(tab)}")
        print("Scrolling to load all content...")
        html = await _scroll_and_capture(tab)
        if not html:
            raise RuntimeError("empty HTML content captured")
        save_path = OUT_DIR / HTML_FILENAME
        body = html.encode("utf-8")
        _write_atomic(save_path, body)
        top_n = await tab.evaluate("document.querySelectorAll('#allReports > div.srFlyTop').length")
        bot_n = await tab.evaluate("document.querySelectorAll('#allReports > div.srFlyBottom').length")
        print(f"\nHTML saved: {save_path} ({len(body):,} bytes)")
        print(f"Live cards: srFlyTop={top_n} srFlyBottom={bot_n} total={int(top_n or 0) + int(bot_n or 0)}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
