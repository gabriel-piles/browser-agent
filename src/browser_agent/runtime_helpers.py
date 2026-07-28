"""Typed signatures for the vendored runtime helpers inlined at emit time.

The LLM imports from this module so it sees real function signatures —
sync vs async, return types, parameter names — instead of guessing from
prose descriptions in the system prompt. The implementations are stubs
(``raise NotImplementedError``); they are NEVER called because the import
is stripped (see :mod:`browser_agent.adapters.emitted_strip_imports`)
before the vendored blocks are prepended at emit time. The module exists
solely as a typed contract anchor.

Keep these signatures in lockstep with the vendored blocks in
:mod:`emitted_save_record`, :mod:`emitted_save_html`,
:mod:`emitted_pdf_download`, :mod:`emitted_page_wait`, and
:mod:`emitted_clean_launch`. The vendored blocks are the source of truth
for the implementations; this module is the source of truth for the
contract the LLM programs against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

Tab = Any  # zendriver.Tab
Browser = Any  # zendriver.Browser


def save_record(source_url: str, data: dict) -> None:
    """Persist one entity's metadata into the shared SQLite store.

    Synchronous — do NOT await. Returns ``None``. Upserts by
    ``source_url`` (PRIMARY KEY — re-runs replace, not duplicate).
    ``data`` is a JSON-serializable dict of metadata fields; multi-value
    fields MUST be a Python list of strings, never a comma-joined string.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def save_page_html(
    tab: Tab,
    save_path: str | Path,
    source_url: str,
    filename: str | None = None,
    card_selector: str | None = None,
) -> dict:
    """Save the current page's HTML to the ``save_path`` directory.

    Uses the REAL browser tab — never an HTTP client. ``save_path`` is
    the downloads DIRECTORY; the on-disk filename is
    ``html_<sha1(source_url)[:12]>.html`` (or ``filename`` when passed).
    Idempotent (skips when the file already exists).

    ALWAYS scrolls the page top-to-bottom before capturing, so lazy-
    loaded content is present in the DOM. When ``card_selector`` is
    passed, snapshots each viewport and consolidates in-browser for
    virtualized lists (react-window / react-virtualized) that unmount
    off-screen nodes.

    Before capture, waits for SPA-rendered metadata to finish binding
    (bounded 8 s timeout, no-op on non-SPA pages) and strips the
    embedded PDF viewer (``#pdf-container``) so the saved HTML carries
    metadata and text but not expiring PDF page-image URLs.

    Returns ``{"size": int, "skipped": bool, "reason": str,
    "saved_path": str}``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def download_pdf_curl_cffi(
    url: str,
    save_path: str | Path,
    tab: Tab | None = None,
) -> dict:
    """Download ``url`` into directory ``save_path`` via curl_cffi.

    Chrome TLS impersonation; cookies extracted from ``tab`` when passed.
    ``save_path`` is the downloads DIRECTORY; the on-disk filename is
    ``pdf_<sha1(url)[:12]>.pdf``. Idempotent (skips when the file
    already exists). Raises ``RuntimeError`` on failure.

    Returns ``{"size": int, "skipped": bool, "reason": str,
    "saved_path": str}``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def download_pdf_browser(
    tab: Tab,
    url: str,
    save_path: str | Path,
) -> dict:
    """Download ``url`` into directory ``save_path`` via the browser's fetch().

    Routes through Chrome's native network stack (TLS fingerprint,
    cookies, JS-challenge clearance) via ``tab.evaluate``. Bypasses
    Cloudflare/Akamai WAF. The tab MUST have navigated to the target
    domain first. ``save_path`` is the downloads DIRECTORY; the on-disk
    filename is ``pdf_<sha1(url)[:12]>.pdf``. Idempotent. Raises
    ``RuntimeError`` on failure.

    Returns ``{"size": int, "skipped": bool, "reason": str,
    "saved_path": str}``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def wait_for_page_ready(
    tab: Tab,
    url: str | None = None,
    timeout: float = 30.0,
    quiet_window_ms: int = 500,
) -> None:
    """Block until the active navigation has loaded and the network is idle.

    Drop-in for ``tab.sleep(...)`` after every ``tab.get(url)``. Waits for
    the frame to stop loading AND the network to be quiet for
    ``quiet_window_ms``. Pass ``url`` so same-URL reloads are handled.
    Returns ``None``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def wait_for_anchors(
    tab: Tab,
    selector: str,
    timeout: float = 8.0,
    poll_interval: float = 0.2,
    required_polls: int = 2,
) -> tuple[int, str]:
    """Block until ``selector`` matches at least one non-empty element.

    Polls ``selector`` every ``poll_interval`` seconds; returns once the
    match count is non-zero for ``required_polls`` consecutive polls OR
    ``timeout`` elapses. Returns ``(matched_count, sample_text)``. Raises
    ``TimeoutError`` when the timeout elapses with zero matches.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def prepare_page_wait(tab: Tab) -> None:
    """Attach the CDP tracker to ``tab`` BEFORE the first navigation.

    Call once at the top of ``main`` — before any ``tab.get(url)`` — so
    the tracker receives the ``frameStoppedLoading`` and ``Network.*``
    events for the first navigation. Returns ``None``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")


async def start_browser(
    headless: bool | None = None,
    user_data_dir: str | None = None,
) -> Browser:
    """Launch a clean Chromium and connect zendriver. Replaces ``zd.start()``.

    Launches with only ``--remote-debugging-port`` and ``--user-data-dir``
    (no automation-flagging args). ``headless`` defaults to the
    ``ZENDRIVER_HEADLESS`` env var. The returned ``Browser``'s ``.stop()``
    also kills the Chromium process. Returns a zendriver ``Browser``.
    """
    raise NotImplementedError("inlined at emit time; import is for typed signatures only")
