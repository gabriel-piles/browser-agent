"""Save the current page's HTML using the real browser tab.

Moved verbatim from ``browser_agent.adapters.emitted_save_html``. Uses
the real browser tab (``tab.get_content()``) — never an HTTP client — so
sites behind Cloudflare/Akamai WAF return the real DOM, not a challenge
page.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from script_tools._file_utils import (
    _existing_size,
    _html_filename_for,
    _write_atomic,
)

# In-browser DOM consolidation. Reads the accumulated snapshots from
# window.__htmlCaptures, uses the LAST (most complete) snapshot as the
# base document, then prepends only NEW (unseen by outerHTML) card nodes
# from earlier snapshots via insertAdjacentHTML('afterbegin', …) — this
# preserves top-to-bottom document order while catching cards that were
# unmounted by a virtualizer. Strips inert elements. Only the selector is
# interpolated; all HTML stays in the browser.
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

# Per-viewport snapshot. The scroll loop calls this at each position; it
# grabs the serialized DOM and stashes it on window so the consolidator
# can read the whole list in one final evaluate (avoids serializing N
# huge strings through Python — only the final result crosses the wire).
_SNAPSHOT_JS = """\
    (() => {
        window.__htmlCaptures = window.__htmlCaptures || [];
        window.__htmlCaptures.push(document.documentElement.outerHTML);
        return window.__htmlCaptures.length;
    })()"""

# Idempotent readiness instrumentation: a MutationObserver counts DOM
# mutations and a PerformanceObserver counts resource-load completions.
# Both count as "activity" — a page whose metadata XHR is still in
# flight is NOT ready even when the DOM is momentarily quiet (the XHR
# completion entry fires when it lands, resetting the quiet streak).
# buffered:true makes the observer deliver the backlog once on install;
# the first poll absorbs it.
_SPA_INSTALL_JS = """\
    (() => {
        if (window.__spaMutObs) return true;
        window.__spaMutCount = 0;
        window.__spaResCount = 0;
        window.__spaMutObs = new MutationObserver(() => { window.__spaMutCount++; });
        window.__spaMutObs.observe(document.documentElement,
            {subtree: true, childList: true, attributes: true, characterData: true});
        try {
            window.__spaResObs = new PerformanceObserver(() => { window.__spaResCount++; });
            window.__spaResObs.observe({type: 'resource', buffered: true});
        } catch (e) { window.__spaResObs = null; }
        return true;
    })()"""

# One-shot SPA readiness poll: reads-and-resets the MutationObserver and
# PerformanceObserver counters, snapshots visible-text length and visible
# loader presence. Serialized as JSON so the whole snapshot crosses the
# wire as a single string (zendriver returns it verbatim).
_SPA_POLL_JS = """\
    (() => {
        const muts = window.__spaMutCount || 0;
        const res = window.__spaResCount || 0;
        window.__spaMutCount = 0;
        window.__spaResCount = 0;
        const body = document.body;
        const textLen = body ? (body.innerText || '').trim().length : 0;
        let loader = false;
        if (body) {
            const els = body.querySelectorAll('[class*="loading"], [class*="spinner"], [class*="loader"]');
            for (const el of els) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                if (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none') {
                    loader = true;
                    break;
                }
            }
        }
        return JSON.stringify({muts: muts, res: res, textLen: textLen, loader: loader});
    })()"""


def _selector_probe(selector):
    """Reduce a CSS selector to a substring probe: the rightmost simple selector.

    ``.document__credits metadata-item`` -> ``<metadata-item``;
    ``.doc-details .field`` -> ``field``; ``#details`` -> ``details``.
    """
    token = re.split(r"[\s>+~]+", selector.strip())[-1]
    m = re.search(r"[.#]([A-Za-z0-9_-]+)", token)
    if m:
        return m.group(1)
    tag = re.match(r"[A-Za-z][A-Za-z0-9-]*", token)
    return f"<{tag.group(0)}" if tag else ""


def _file_has_selector(path, selector):
    """True when the saved HTML file plausibly contains a match for ``selector``."""
    probe = _selector_probe(selector)
    if not probe:
        return True
    try:
        return probe in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


async def _ensure_tab_visible(tab):
    """Activate the tab so rendering-dependent framework binding can run.

    Hidden background tabs never fire IntersectionObserver or
    requestAnimationFrame, so Aurelia/React late-bound content (e.g. the
    vLex/Corte IDH ``metadata-item`` block) NEVER renders while the tab is
    hidden — the DOM keeps ``<!--anchor-->`` placeholders no matter how long
    a gate waits. Once rendered, the content persists in the DOM even if the
    tab is hidden again. NOTE: "one activation before the gate suffices"
    is only true when the caller serializes the gate phase with a shared
    ``asyncio.Lock`` (rule 15h) — without the lock, concurrent per-tab
    ``bring_to_front()`` calls steal foreground and N-1 tabs never render.
    Capture-anyway: a failed activation must not abort a document row.
    """
    try:
        await tab.bring_to_front()
    except Exception:
        pass


async def save_page_html(tab, save_path, source_url, filename=None, card_selector=None, ready_selector=None):
    """Save the current page's HTML to ``save_path`` directory.

    Uses the REAL browser tab — never an HTTP client (curl_cffi,
    requests, httpx, aiohttp). Sites behind Cloudflare / Akamai WAF
    would block a non-browser request and the HTML would be a challenge
    page. The browser tab carries the same TLS fingerprint, cookies,
    and JS-challenge clearance as the PDF download path.

    ``save_path`` is the downloads DIRECTORY (e.g. ``out_dir``); the
    on-disk filename is ``html_<sha1(source_url)[:12]>.html`` —
    deterministic, collision-safe, same scheme as PDF naming. When
    ``filename`` is passed it is used instead (caller-supplied override).

    Idempotent: skips the write when the target file already exists and
    is non-empty. Writes are atomic (temp + rename).

    SCROLL-BY-DEFAULT: the helper ALWAYS scrolls the page top-to-bottom
    before capturing, so lazy-loaded content (IntersectionObserver,
    infinite scroll, "load more") is present in the DOM at capture
    time. A single ``get_content()`` without scrolling misses off-screen
    cards; scrolling first guarantees they are mounted and captured.

    VIRTUALIZED LISTS: when ``card_selector`` (CSS) is passed, the
    helper snapshots the DOM at each viewport during the scroll and
    consolidates all snapshots in-browser into one deduplicated document
    (by card outerHTML). This is needed for react-window / react-
    virtualized lists that only mount a visible slice per viewport and
    unmount off-screen nodes. When ``card_selector`` is omitted, a single
    capture after scrolling to the bottom suffices (the scroll already
    triggered all lazy loads into the DOM).

    SPA METADATA: before capture, waits for SPA-rendered metadata to
    finish binding by polling DOM-mutation and resource-load counters
    until the page is quiescent with visible text and no visible loader
    (bounded 15 s timeout; static pages pass after ~1 s of quiet
    polls). This prevents capturing an unrendered SPA shell instead of
    the populated metadata on SPA sites like vLex / Corte IDH where
    ``networkIdle`` fires before the framework stamps the DOM. This
    generic quiescence wait is a HEURISTIC — it can pass during a
    quiet gap BEFORE the framework binds a late metadata XHR response,
    producing an HTML capture that contains only placeholder comments
    (e.g. ``<!--anchor-->``) instead of the late-bound metadata. When
    the page exposes a concrete element that only appears after the
    binding pass, pass ``ready_selector`` (see below) instead of
    relying on the heuristic alone.

    READY SELECTOR: when ``ready_selector`` (CSS) is passed, the helper
    waits up to 15 s for the selector to match an element WITH RENDERED
    CONTENT — non-empty text or at least one element child — before
    capture (never raises — captures whatever is there on timeout, so
    one slow page never aborts a document row). An element that exists
    in the initial shell carrying only placeholder comments (e.g.
    ``<!--anchor-->``) or empty text does NOT pass the gate, so an
    empty static container still gates until the binding pass fills
    it. The wait runs twice: once up front and again immediately
    before the final capture. The selector MUST name the late-bound
    metadata element itself (e.g.
    ``ready_selector=".document__credits metadata-item"``), not a
    static container that is already present in the empty shell — a
    static container would match immediately and defeat the gate. The
    selector MUST name an element that ONLY EXISTS after the binding
    pass — the framework's custom-element tag (e.g.
    ``ready_selector="metadata-item"`` or
    ``ready_selector=".document__credits metadata-item"``). NEVER name a
    class that also matches SERVER-RENDERED/static duplicates of the same
    metadata elsewhere on the page: on vLex, ``.document__credits-item``
    matches the static ``#original-text`` metadata block from the initial
    shell, so the gate passes instantly while the late-bound block stays
    an ``<!--anchor-->`` placeholder (and the self-heal skip check keeps
    the broken file forever). The
    skip path also self-heals: when ``ready_selector`` is passed and
    the existing on-disk file does NOT contain the selector (the
    capture predates the binding pass), the file is treated as absent
    and re-captured.

    TAB VISIBILITY: when ``ready_selector`` is passed, the helper
    first activates the tab (``tab.bring_to_front()``) because hidden
    background tabs never run IntersectionObserver/requestAnimationFrame —
    framework binding that depends on them (Aurelia repeaters on
    vLex/Corte IDH, React lazy mounts) only happens in the visible tab.
    Binding persists once rendered, BUT on a fresh navigation the binding
    has not fired yet and cannot fire while another tab holds foreground —
    so parallel workers that each call ``bring_to_front()`` race and only
    the last tab to activate actually renders. The CALLER must serialize
    the navigate + activate + gate phase with a shared ``asyncio.Lock``
    (rule 15h); this helper's own activation is a belt-and-suspenders
    safety net, not a substitute for the lock.

    PDF VIEWER STRIP: before capture, removes ``#pdf-container`` and
    ``.pdf-viewer`` from the DOM so the saved HTML carries metadata and
    text but not hundreds of expiring PDF page-image ``<img>`` URLs.
    No-op when the element does not exist.

    Returns a dict with ``saved_path`` so the caller can store the
    exact ``html_filename`` in the DB row:

        result = await save_page_html(tab, out_dir, page_url)
        save_record(..., {"html_filename": Path(result["saved_path"]).name,
                          "source_page_url": source_url, ...})

    For virtualized lists:

        result = await save_page_html(
            tab, out_dir, page_url, card_selector=".card")

    On SPA pages with late-bound metadata:

        result = await save_page_html(
            tab, out_dir, page_url,
            ready_selector=".document__credits metadata-item")
    """
    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    name = filename if filename else _html_filename_for(source_url)
    save_path = save_dir / name

    existing = _existing_size(save_path)
    if existing > 0 and (ready_selector is None or _file_has_selector(save_path, ready_selector)):
        return {"size": existing, "skipped": True, "reason": "already_saved", "saved_path": str(save_path)}
    if ready_selector:
        await _ensure_tab_visible(tab)
        await _wait_for_ready_selector(tab, ready_selector)

    if card_selector:
        html = await _capture_virtualized_html(tab, card_selector, ready_selector)
    else:
        html = await _capture_scrolled_html(tab, ready_selector)
    if not html:
        raise RuntimeError(f"empty HTML content for {source_url}")
    body = html.encode("utf-8")
    _write_atomic(save_path, body)
    return {"size": len(body), "skipped": False, "reason": "saved", "saved_path": str(save_path)}


async def _capture_simple_html(tab):
    """Single-shot DOM capture via get_content with evaluate fallback."""
    try:
        return await tab.get_content()
    except Exception:
        return await tab.evaluate("document.documentElement.outerHTML")


async def _scroll_to_bottom(tab):
    """Scroll the page top-to-bottom, waiting for lazy-loaded content.

    Returns True if the page grew during scrolling (content was loaded
    dynamically), False if the page was already complete.
    """
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.4)
    prev_height = -1
    stable = 0
    grew = False
    max_iters = 200
    for _ in range(max_iters):
        height = await tab.evaluate("document.body ? document.body.scrollHeight : 0")
        height = int(height) if height is not None else 0
        if height != prev_height and prev_height > 0:
            grew = True
        at_bottom = await tab.evaluate(
            "document.body ? (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2 : true"
        )
        if height == prev_height:
            stable += 1
        else:
            stable = 0
        if (at_bottom and stable >= 2) or stable >= 4:
            break
        prev_height = height
        await tab.evaluate("window.scrollBy(0, window.innerHeight)")
        await tab.sleep(1.0)
    return grew


async def _strip_reveal_styles(tab):
    """Clear scroll-reveal inline styles so off-screen cards render in the saved HTML.

    Libraries like scrollreveal.js set ``opacity:0`` and a 3D
    ``transform`` on every element they animate, flipping them to
    ``opacity:1`` only as the element scrolls into view. When the
    captured HTML is later opened locally, the reveal only runs for
    the first viewport, so every below-the-fold card stays invisible
    (present in the DOM, ``opacity:0``). Removing the reveal inline
    style on all elements makes the saved file render every card
    regardless of scroll position.
    """
    await tab.evaluate(
        "document.querySelectorAll('*').forEach(el => {"
        " const s = el.style;"
        " if (s.opacity === '0') { s.opacity = ''; }"
        " if (s.transform && s.transform.startsWith('matrix3d')) { s.transform = ''; }"
        " if (s.visibility === 'hidden') { s.visibility = ''; }"
        "})"
    )


_SPA_READY_TIMEOUT_S = 15.0
_SPA_POLL_INTERVAL_S = 0.3
_SPA_QUIET_POLLS = 3
_SPA_LOADER_GRACE_S = 4.0
_READY_SELECTOR_TIMEOUT_S = 15.0
_READY_SELECTOR_POLL_S = 0.3
_READY_SELECTOR_STABLE_POLLS = 2


async def _spa_install_observer(tab):
    """Idempotently install the mutation/resource activity counters."""
    await tab.evaluate(_SPA_INSTALL_JS)


async def _spa_poll(tab):
    """Read-and-reset the mutation counter; snapshot text and loader state."""
    raw = await tab.evaluate(_SPA_POLL_JS)
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


async def _wait_for_spa_ready(tab):
    """Wait for SPA-rendered metadata to finish binding.

    Polls activity counters until the page is quiescent (three
    consecutive polls with zero DOM mutations AND zero resource-load
    completions) AND the page has visible text, while vetoing visible
    loader/spinner elements. The resource counter is load-bearing: a
    metadata XHR in flight leaves the DOM momentarily quiet, so DOM
    quiescence alone can pass before the framework binds the response.
    The text floor guards the opposite case (a static shell is also
    quiet). Bounded by ``_SPA_READY_TIMEOUT_S``; never raises, so
    capture on timeout is never worse than today.
    """
    await _spa_install_observer(tab)
    deadline = time.monotonic() + _SPA_READY_TIMEOUT_S
    grace_deadline = None
    quiet = 0
    while True:
        snap = await _spa_poll(tab)
        quiet = quiet + 1 if snap["muts"] == 0 and snap["res"] == 0 else 0
        content_ready = quiet >= _SPA_QUIET_POLLS and snap["textLen"] > 0
        if content_ready and not snap["loader"]:
            return
        if content_ready and grace_deadline is None:
            grace_deadline = time.monotonic() + _SPA_LOADER_GRACE_S
        now = time.monotonic()
        if now >= deadline or (grace_deadline is not None and now >= grace_deadline):
            return
        await tab.sleep(_SPA_POLL_INTERVAL_S)


async def _wait_for_ready_selector(tab, selector):
    """Block until ``selector`` matches an element with rendered content; return silently on timeout.

    Content gate: at least one matched element must have non-empty
    textContent OR at least one element child. An Aurelia shell holding
    only ``<!--anchor-->`` comments fails the gate, so naming the
    metadata CONTAINER (not just the item element) also waits for the
    binding pass. Capture-anyway semantics: the timeout is swallowed so
    one slow page never aborts a document row.
    """
    if not selector:
        return
    js = (
        "(() => { const els = document.querySelectorAll(" + json.dumps(selector) + ");"
        " for (const el of els) {"
        " if ((el.textContent || '').trim().length > 0 || el.querySelector('*')) return true;"
        " } return false; })()"
    )
    deadline = time.monotonic() + _READY_SELECTOR_TIMEOUT_S
    stable = 0
    while time.monotonic() < deadline:
        try:
            if await tab.evaluate(js):
                stable += 1
                if stable >= _READY_SELECTOR_STABLE_POLLS:
                    return
            else:
                stable = 0
        except Exception:
            stable = 0
        await tab.sleep(_READY_SELECTOR_POLL_S)


async def _strip_pdf_viewer(tab):
    """Remove the embedded PDF viewer from the DOM before capture.

    Sites like vLex render the full PDF inside a ``#pdf-container``
    element as hundreds of ``<img>`` tags pointing to S3 pre-signed
    URLs. These are the rendered pages of the document being
    downloaded as a PDF — including them in the saved HTML bloats the
    file with expiring image URLs that 404 within an hour. Stripping
    the viewer before ``get_content()`` keeps the metadata, header,
    tabs and text while dropping only the PDF page images.

    No-op when the element does not exist.
    """
    await tab.evaluate("document.querySelectorAll('#pdf-container, .pdf-viewer').forEach(el => el.remove())")


async def _capture_scrolled_html(tab, ready_selector=None):
    """Scroll to bottom (triggering lazy loads), then capture the full DOM.

    After scrolling, all IntersectionObserver / infinite-scroll content
    is mounted in the DOM, so a single ``get_content()`` captures the
    complete page. This handles the common lazy-load case without the
    overhead of per-viewport snapshots.
    """
    await _wait_for_spa_ready(tab)
    await _scroll_to_bottom(tab)
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.3)
    await _strip_reveal_styles(tab)
    await _wait_for_spa_ready(tab)
    await _strip_pdf_viewer(tab)
    await _wait_for_ready_selector(tab, ready_selector)
    return await _capture_simple_html(tab)


async def _capture_virtualized_html(tab, card_selector, ready_selector=None):
    """Scroll top-to-bottom, snapshot per viewport, consolidate in-browser.

    For virtualized lists (react-window / react-virtualized) that only
    mount a visible slice per viewport and unmount off-screen nodes. Each
    viewport renders a disjoint slice, and the in-browser consolidator
    merges all slices into one deduplicated document using the last
    (most complete) snapshot as base. Every card that was ever rendered
    appears in the output.
    """
    await _wait_for_spa_ready(tab)
    await _wait_for_ready_selector(tab, ready_selector)
    await _strip_pdf_viewer(tab)
    await tab.evaluate("window.__htmlCaptures = []")
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.4)
    prev_height = -1
    stable = 0
    max_iters = 200
    for _ in range(max_iters):
        await _strip_reveal_styles(tab)
        await tab.evaluate(_SNAPSHOT_JS)
        height = await tab.evaluate("document.body ? document.body.scrollHeight : 0")
        height = int(height) if height is not None else 0
        at_bottom = await tab.evaluate(
            "document.body ? (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2 : true"
        )
        if height == prev_height:
            stable += 1
        else:
            stable = 0
        if (at_bottom and stable >= 2) or stable >= 4:
            break
        prev_height = height
        await tab.evaluate("window.scrollBy(0, window.innerHeight)")
        await tab.sleep(1.0)
    await _strip_reveal_styles(tab)
    await tab.evaluate(_SNAPSHOT_JS)
    count = await tab.evaluate("(window.__htmlCaptures || []).length")
    count = int(count) if count is not None else 0
    if count == 0:
        return await _capture_simple_html(tab)
    await _wait_for_ready_selector(tab, ready_selector)
    consolidated = await tab.evaluate(_CONSOLIDATE_JS.format(selector_literal=json.dumps(card_selector)))
    try:
        await tab.evaluate("delete window.__htmlCaptures")
    except Exception:
        pass
    return consolidated or await _capture_simple_html(tab)
