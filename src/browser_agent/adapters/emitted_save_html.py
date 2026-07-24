"""Self-contained page-HTML capture helper inlined into every emitted script.

The final script the operator runs from ``data/runs/<run>/scripts/`` is
self-contained by contract and MUST NOT import from this project. When a
task downloads PDFs, the operator also wants the source HTML of the page
where each PDF was found attached as a **supporting file** on the same
Uwazi entity. This helper is shipped as a plain-Python string and
prepended to every emitted ``python_code``, mirroring the
:mod:`emitted_pdf_download` pattern.

The helper uses the **real browser tab** (``tab.get_content()``) to
capture the full serialized DOM — never an HTTP client (curl_cffi,
requests, httpx, aiohttp). Sites behind Cloudflare / Akamai WAF would
return a challenge page to a non-browser client; the browser tab carries
the same TLS fingerprint, cookies, and JS-challenge clearance as the
PDF download path, so the captured HTML matches the exact state from
which the PDF was downloaded.
"""

from __future__ import annotations

from browser_agent.adapters.emitted_snippets import (
    ATOMIC_WRITE_SNIPPET,
    EXISTING_SIZE_SNIPPET,
    HTML_FILENAME_SNIPPET,
)

_HELPERS = (
    "import hashlib\n"
    "import os as _os\n"
    "from pathlib import Path\n"
    "\n\n"
    f"{HTML_FILENAME_SNIPPET}\n\n"
    f"{EXISTING_SIZE_SNIPPET}\n\n"
    f"{ATOMIC_WRITE_SNIPPET}"
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


_SAVE_HTML = '''\


async def save_page_html(tab, save_path, source_url, filename=None, card_selector=None):
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

    Returns a dict with ``saved_path`` so the caller can store the
    exact ``html_filename`` in the DB row:

        result = await save_page_html(tab, out_dir, page_url)
        save_record(..., {"html_filename": Path(result["saved_path"]).name, ...})

    For virtualized lists:

        result = await save_page_html(
            tab, out_dir, page_url, card_selector=".card")
    """
    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    name = filename if filename else _html_filename_for(source_url)
    save_path = save_dir / name

    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_saved",
                "saved_path": str(save_path)}

    if card_selector:
        html = await _capture_virtualized_html(tab, card_selector)
    else:
        html = await _capture_scrolled_html(tab)
    if not html:
        raise RuntimeError(f"empty HTML content for {source_url}")
    body = html.encode("utf-8")
    _write_atomic(save_path, body)
    return {"size": len(body), "skipped": False, "reason": "saved",
            "saved_path": str(save_path)}


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
        height = await tab.evaluate("document.body.scrollHeight")
        height = int(height) if height is not None else 0
        if height != prev_height and prev_height > 0:
            grew = True
        at_bottom = await tab.evaluate(
            "(window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2")
        if height == prev_height:
            stable += 1
        else:
            stable = 0
        if (at_bottom and stable >= 2) or stable >= 4:
            break
        prev_height = height
        await tab.evaluate("window.scrollBy(0, window.innerHeight)")
        await tab.sleep(0.6)
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


async def _capture_scrolled_html(tab):
    """Scroll to bottom (triggering lazy loads), then capture the full DOM.

    After scrolling, all IntersectionObserver / infinite-scroll content
    is mounted in the DOM, so a single ``get_content()`` captures the
    complete page. This handles the common lazy-load case without the
    overhead of per-viewport snapshots.
    """
    await _scroll_to_bottom(tab)
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.3)
    await _strip_reveal_styles(tab)
    return await _capture_simple_html(tab)

async def _capture_virtualized_html(tab, card_selector):
    """Scroll top-to-bottom, snapshot per viewport, consolidate in-browser.

    For virtualized lists (react-window / react-virtualized) that only
    mount a visible slice per viewport and unmount off-screen nodes. Each
    viewport renders a disjoint slice, and the in-browser consolidator
    merges all slices into one deduplicated document using the last
    (most complete) snapshot as base. Every card that was ever rendered
    appears in the output.
    """
    import json as _json
    await tab.evaluate("window.__htmlCaptures = []")
    await tab.evaluate("window.scrollTo(0, 0)")
    await tab.sleep(0.4)
    prev_height = -1
    stable = 0
    max_iters = 200
    for _ in range(max_iters):
        await _strip_reveal_styles(tab)
        await tab.evaluate(_SNAPSHOT_JS)
        height = await tab.evaluate("document.body.scrollHeight")
        height = int(height) if height is not None else 0
        at_bottom = await tab.evaluate(
            "(window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2")
        if height == prev_height:
            stable += 1
        else:
            stable = 0
        if (at_bottom and stable >= 2) or stable >= 4:
            break
        prev_height = height
        await tab.evaluate("window.scrollBy(0, window.innerHeight)")
        await tab.sleep(0.6)
    await _strip_reveal_styles(tab)
    await tab.evaluate(_SNAPSHOT_JS)
    count = await tab.evaluate("(window.__htmlCaptures || []).length")
    count = int(count) if count is not None else 0
    if count == 0:
        return await _capture_simple_html(tab)
    consolidated = await tab.evaluate(
        _CONSOLIDATE_JS.format(selector_literal=_json.dumps(card_selector)))
    try:
        await tab.evaluate("delete window.__htmlCaptures")
    except Exception:
        pass
    return consolidated or await _capture_simple_html(tab)'''


EMITTED_SAVE_HTML_BLOCK = (
    "# ── BEGIN emitted save-page-html helper (vendored from browser_agent) ──\n"
    f"{_HELPERS}"
    f"{_SAVE_HTML}\n"
    "# ── END emitted save-page-html helper ──\n\n"
)


def with_emitted_save_html(python_code: str) -> str:
    """Prepend the vendored save-page-html helper to ``python_code``.

    Both the in-process validation runner and the final-script emit
    path call this so the helper appears at the top of every script
    that runs. Idempotent: if the script already contains the block
    marker it is returned unchanged.
    """
    if "BEGIN emitted save-page-html helper" in python_code:
        return python_code
    return f"{EMITTED_SAVE_HTML_BLOCK}{python_code}"
