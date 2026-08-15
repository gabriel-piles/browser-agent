"""PDF and supporting-document download helpers for emitted scripts.

Moved verbatim from ``browser_agent.adapters.emitted_pdf_download``.
Two strategies: curl_cffi (Chrome TLS impersonation) and browser_fetch
(CDP Network.loadNetworkResource + in-tab fetch + curl_cffi fallback).
``curl_cffi`` is imported lazily inside the functions so scripts using
only ``download_pdf_browser`` work without curl_cffi installed.

Non-PDF documents (``.doc``/``.docx``/``.rtf``/…) use the same two
strategies via the ``download_file_*`` twins; the only differences are
the on-disk name (``file_filename_for``, a pure function of the URL)
and the skipped PDF magic check (a non-PDF body must not raise).
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from script_tools._file_utils import (
    _assert_pdf_magic,
    _existing_size,
    _pdf_filename_for,
    _write_atomic,
    file_filename_for,
)

_PDF_DOWNLOAD_TIMEOUT_S = 90.0
_PDF_DOWNLOAD_RETRIES = 3
_PDF_DOWNLOAD_RETRY_DELAY_S = 1.5
_CDP_READ_TIMEOUT_S = 30.0

_BLOCK_STREAK_LIMIT = 8
_DOWNLOAD_DELAY_MIN_S = 2.0
_DOWNLOAD_DELAY_MAX_S = 5.0
_BLOCK_COOLDOWN_S = 30.0

# Interactive Cloudflare bypass: when the 403 streak hits the limit,
# navigate the visible tab to the blocked URL so the operator can
# manually click the Cloudflare checkbox. Poll until the challenge
# clears, then reset the streak and let the caller retry.
_CF_MAX_RETRIES = 3
_CF_INTERACTIVE_TIMEOUT_S = 300.0
_CF_POLL_INTERVAL_S = 2.0
_CF_TITLES = ("just a moment", "attention required", "checking your browser")
_CF_PROMPT = (
    "\n" + "=" * 72 + "\n"
    "CLOUDFLARE CHALLENGE DETECTED on {url}\n"
    "The browser is paused. Please go to the visible Chromium window\n"
    "and click the Cloudflare 'Verify you are human' checkbox.\n"
    "The script will automatically resume once the challenge clears.\n"
    "Timeout: {timeout:.0f}s\n" + "=" * 72 + "\n"
)

_consecutive_403 = 0


def _resolve_url(url, tab=None):
    """Resolve a site-relative URL against the tab's current page URL.

    CDP ``Network.loadNetworkResource`` and ``curl_cffi`` both require
    absolute URLs; site-relative paths (``/en/iachr/...``) are rejected.
    When ``url`` already has a scheme or ``tab`` has no URL, return it
    unchanged.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if parts.scheme:
        return url
    base = getattr(tab, "url", None) or ""
    if not base:
        return url
    return urljoin(base, url)


def _is_localhost(url: str) -> bool:
    """True when ``url`` points at localhost/127.0.0.1/0.0.0.0."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def _upgrade_to_https(url: str) -> str:
    """Upgrade ``http://`` to ``https://`` for non-localhost URLs.

    Real sites need HTTPS to avoid mixed-content blocking; local fixture
    servers serve HTTP only and must not be upgraded.
    """
    if url.startswith("http://") and not _is_localhost(url):
        return "https://" + url[7:]
    return url


def _is_http_403(exc):
    """True when ``exc``'s message carries an HTTP 403 status."""
    return "HTTP 403" in str(exc)


def _is_cloudflare_title(title: str) -> bool:
    """True when ``title`` matches a known Cloudflare challenge page."""
    if not title:
        return False
    lower = title.lower()
    return any(marker in lower for marker in _CF_TITLES)


async def _wait_for_cloudflare_clear(tab, url):
    """Navigate ``tab`` to ``url`` and pause for the operator to clear Cloudflare.

    When a Cloudflare challenge blocks downloads, the HTTP 403 responses
    come from Cloudflare's edge, not the origin server. The browser tab
    itself may or may not show the challenge page (curl_cffi fetches
    bypass the tab). This function navigates the visible browser tab to
    the blocked URL so the Cloudflare challenge renders in the browser
    window, then polls the page title until the operator manually clicks
    the checkbox. Once the challenge clears, the streak is reset and the
    caller retries the download.

    If the tab is None or the page never shows a challenge (already
    cleared, or the challenge doesn't render), the function returns
    after a brief wait so the caller's retry gets fresh cookies.
    """
    global _consecutive_403
    if tab is None:
        print(_CF_PROMPT.format(url=url, timeout=_CF_INTERACTIVE_TIMEOUT_S))
        await asyncio.sleep(_CF_INTERACTIVE_TIMEOUT_S)
        _consecutive_403 = 0
        return
    try:
        await tab.get(url)
        await tab.sleep(1.0)
        title = await tab.evaluate("document.title") or ""
    except Exception:
        title = ""
    if not _is_cloudflare_title(title):
        _consecutive_403 = 0
        await asyncio.sleep(2.0)
        return
    print(_CF_PROMPT.format(url=url, timeout=_CF_INTERACTIVE_TIMEOUT_S))
    deadline = asyncio.get_event_loop().time() + _CF_INTERACTIVE_TIMEOUT_S
    while asyncio.get_event_loop().time() < deadline:
        await tab.sleep(_CF_POLL_INTERVAL_S)
        try:
            title = await tab.evaluate("document.title") or ""
        except Exception:
            title = ""
        if not _is_cloudflare_title(title):
            print("Cloudflare challenge cleared. Resuming downloads...")
            _consecutive_403 = 0
            return
    print("Cloudflare interactive wait timed out. Retrying with fresh session...")
    _consecutive_403 = 0


async def _track_download_outcome(exc, tab=None, url=None):
    """Track consecutive 403 failures; return True when Cloudflare was cleared.

    ``exc=None`` (success) resets the streak; an HTTP 403 RuntimeError
    increments it; any other error leaves it unchanged. When the streak
    hits ``_BLOCK_STREAK_LIMIT``, it pauses for the operator to
    manually clear the Cloudflare challenge in the visible browser
    (``_wait_for_cloudflare_clear``) and returns ``True`` so the caller
    retries the download with fresh cookies. Returns ``False`` otherwise.
    """
    global _consecutive_403
    if exc is None:
        _consecutive_403 = 0
        return False
    if not _is_http_403(exc):
        return False
    _consecutive_403 += 1
    if _consecutive_403 >= _BLOCK_STREAK_LIMIT:
        await _wait_for_cloudflare_clear(tab, url or "")
        return True
    return False


def _resolve_download_dir(save_path):
    """Return the downloads directory for ``save_path``, creating it.

    ``save_path`` is the downloads DIRECTORY (e.g. ``out_dir``). If a
    filename is passed instead, its parent directory is used.
    """
    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir


async def _download_curl(url, save_path, tab, check_magic):
    """Shared curl_cffi download loop: cookie sharing, retries, optional magic check.

    Returns the standard result dict on success; raises ``RuntimeError``
    on final failure. When ``check_magic`` is true, the body must pass
    :func:`_assert_pdf_magic` (PDF path); when false (supporting
    documents) the body is written as-is.

    When the 403 streak hits the limit, ``_track_download_outcome``
    pauses for the operator to clear Cloudflare. After the challenge
    clears, cookies are re-fetched from the tab and the download
    retries with the fresh session.
    """
    from curl_cffi import AsyncSession

    async def _fetch_cookies():
        if tab is None:
            return {}
        try:
            from zendriver.cdp import network as _net

            cdp_cookies = await tab.send(_net.get_cookies([url]))
            return {c.name: c.value for c in cdp_cookies if getattr(c, "name", None) and getattr(c, "value", None)}
        except Exception:
            return {}

    last_exc = None
    for _cf_round in range(_CF_MAX_RETRIES + 1):
        cookies = await _fetch_cookies()
        for attempt in range(1, _PDF_DOWNLOAD_RETRIES + 1):
            try:
                async with AsyncSession() as s:
                    r = await s.get(url, impersonate="chrome", cookies=cookies, timeout=60.0)
            except Exception as e:
                last_exc = RuntimeError(f"curl_cffi request failed for {url}: {e}")
                if attempt < _PDF_DOWNLOAD_RETRIES:
                    await asyncio.sleep(_PDF_DOWNLOAD_RETRY_DELAY_S * attempt)
                continue
            if r.status_code >= 400:
                last_exc = RuntimeError(f"HTTP {r.status_code} for {url}")
                if attempt < _PDF_DOWNLOAD_RETRIES:
                    delay = _PDF_DOWNLOAD_RETRY_DELAY_S * attempt * (3 if r.status_code == 403 else 1)
                    await asyncio.sleep(delay)
                elif r.status_code == 403:
                    await asyncio.sleep(_BLOCK_COOLDOWN_S)
                continue
            body = r.content
            if not body:
                last_exc = RuntimeError(f"empty response for {url}")
                if attempt < _PDF_DOWNLOAD_RETRIES:
                    await asyncio.sleep(_PDF_DOWNLOAD_RETRY_DELAY_S * attempt)
                continue
            _write_atomic(save_path, body)
            if check_magic:
                _assert_pdf_magic(save_path, body, url)
            await _track_download_outcome(None, tab=tab, url=url)
            return {"size": len(body), "skipped": False, "reason": "downloaded", "saved_path": str(save_path)}
        cleared = await _track_download_outcome(last_exc, tab=tab, url=url)
        if not cleared:
            raise last_exc
    raise last_exc


async def download_pdf_curl_cffi(url, save_path, tab=None):
    """Download ``url`` into directory ``save_path`` via curl_cffi.

    The on-disk filename is a deterministic function of ``url``
    (``pdf_<sha1(url)[:12]>.pdf``), NOT the caller-supplied name —
    so re-runs in a different order produce the same path and the
    skip-by-path check stays correct.

    ``save_path`` is the downloads DIRECTORY (e.g. ``out_dir``).
    If a filename is passed instead, its parent directory is used.

    Uses Chrome TLS fingerprint impersonation.  When ``tab`` is
    provided, cookies are extracted from the active browser session
    so cookie-gated / authenticated downloads work.

    Idempotent: if the target file already exists and is non-empty,
    the download is skipped (``skipped=True``).  Writes are atomic
    (temp + rename) so a crash mid-download never leaves a partial
    file.

    Returns a dict with ``saved_path`` (the absolute path written)
    so the caller can store the exact ``pdf_filename`` in the DB:

        result = await download_pdf_curl_cffi(file_url, out_dir, tab)
        save_record(..., {"pdf_filename": Path(result["saved_path"]).name, ...})

    Retries transient failures (network error, HTTP >= 400, empty
    body) up to ``_PDF_DOWNLOAD_RETRIES`` times with linear backoff
    before raising ``RuntimeError`` on final failure.

    When ``_BLOCK_STREAK_LIMIT`` consecutive downloads are blocked with
    HTTP 403, the helper pauses for the operator to manually clear the
    Cloudflare challenge in the visible browser, then retries with
    fresh cookies. Re-running resumes via skip-existing.
    """
    save_dir = _resolve_download_dir(save_path)
    url = _resolve_url(url, tab)
    save_path = save_dir / _pdf_filename_for(url)

    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded", "saved_path": str(save_path)}

    await asyncio.sleep(random.uniform(_DOWNLOAD_DELAY_MIN_S, _DOWNLOAD_DELAY_MAX_S))
    return await _download_curl(url, save_path, tab, check_magic=True)


async def download_file_curl_cffi(url, save_path, tab=None):
    """Download a non-PDF document (``.doc``/``.docx``/``.rtf``/…) into directory ``save_path``.

    Identical contract to :func:`download_pdf_curl_cffi` (retries,
    cookie sharing, idempotent skip-by-path, same result dict) except:
    the on-disk name is ``doc_<sha1(canonical_url)[:12]><ext>``
    (``file_filename_for``) and the body is NOT validated as PDF.
    """
    save_dir = _resolve_download_dir(save_path)
    url = _resolve_url(url, tab)
    save_path = save_dir / file_filename_for(url)

    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded", "saved_path": str(save_path)}

    await asyncio.sleep(random.uniform(_DOWNLOAD_DELAY_MIN_S, _DOWNLOAD_DELAY_MAX_S))
    return await _download_curl(url, save_path, tab, check_magic=False)


async def _fetch_pdf_via_cdp_navigation(tab, url):
    """Fetch ``url`` via CDP Network.loadNetworkResource, bypassing CORS/CSP.

    Routes the request through Chrome's network stack at the browser-process
    level, so renderer-enforced CORS/CSP does not apply and the real TLS
    fingerprint + cookies are used. Returns raw bytes; raises RuntimeError
    on any failure.
    """
    from zendriver.cdp import network as _net
    from zendriver.cdp import io as _io
    from zendriver.cdp import page as _pg

    frame_id = None
    try:
        await tab.send(_pg.enable())
        tree = await tab.send(_pg.get_frame_tree())
        frame_id = tree.frame.id_
    except Exception:
        frame_id = None
    try:
        res = await asyncio.wait_for(
            tab.send(
                _net.load_network_resource(
                    url=url,
                    options=_net.LoadNetworkResourceOptions(
                        disable_cache=True,
                        include_credentials=True,
                    ),
                    frame_id=frame_id,
                )
            ),
            timeout=_PDF_DOWNLOAD_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"CDP load_network_resource timed out for {url}")
    except Exception as exc:
        raise RuntimeError(f"CDP load_network_resource failed for {url}: {exc}") from exc
    if not res.success:
        raise RuntimeError(f"CDP fetch failed net_error={res.net_error} ({res.net_error_name}) for {url}")
    if res.http_status_code and res.http_status_code >= 400:
        raise RuntimeError(f"HTTP {int(res.http_status_code)} for {url}")
    handle = res.stream
    if handle is None:
        raise RuntimeError(f"no stream handle for {url}")
    chunks = []
    offset = None
    while True:
        try:
            b64, data, eof = await asyncio.wait_for(
                tab.send(_io.read(handle, offset=offset, size=None)),
                timeout=_CDP_READ_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"CDP stream read timed out for {url}")
        if data:
            chunks.append(base64.b64decode(data) if b64 else data.encode())
        if eof:
            break
    body = b"".join(chunks)
    if not body:
        raise RuntimeError(f"empty CDP stream for {url}")
    return body


async def _fetch_pdf_once(tab, url):
    """Single attempt: fetch ``url`` in ``tab``, return base64 body or raise RuntimeError."""
    js = (
        f"(async () => {{\\n"
        f"  const r = await fetch({json.dumps(url)}, {{ credentials: 'include' }});\\n"
        f"  if (!r.ok) throw new Error('HTTP ' + r.status);\\n"
        f"  const blob = await r.blob();\\n"
        f"  return await new Promise((res, rej) => {{\\n"
        f"    const reader = new FileReader();\\n"
        f"    reader.onload = () => res(reader.result.split(',')[1]);\\n"
        f"    reader.onerror = () => rej(reader.error);\\n"
        f"    reader.readAsDataURL(blob);\\n"
        f"  }});\\n"
        f"}})()"
    )
    try:
        return await asyncio.wait_for(
            tab.evaluate(js, await_promise=True),
            timeout=_PDF_DOWNLOAD_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"download timed out for {url}") from exc
    except Exception as exc:
        # zendriver wraps JS-side fetch failures (e.g. Cloudflare
        # re-challenges, network resets) as ProtocolException with
        # "TypeError: Failed to fetch" in the message. Convert to
        # RuntimeError so the caller can retry/handle uniformly.
        raise RuntimeError(f"fetch failed for {url}: {exc}") from exc


async def _try_browser_fetch(tab, url, save_path, check_magic=True):
    """Try CDP-bypass fetch, then in-tab fetch; write ``save_path`` atomically.

    The CDP path (:func:`_fetch_pdf_via_cdp_navigation`) is CORS/CSP-proof
    and uses the real TLS fingerprint + cookies, so it is tried first.
    The in-tab ``fetch()`` (:func:`_fetch_pdf_once`) is faster for
    same-origin, non-gated resources and is tried second.  Each path is
    retried up to ``_PDF_DOWNLOAD_RETRIES`` times.  Returns a result
    dict with ``saved_path``; raises ``RuntimeError`` on final failure.
    When ``check_magic`` is true the body must pass :func:`_assert_pdf_magic`
    (PDF path); supporting documents skip it.
    """
    for _fetch, _decode in (
        (_fetch_pdf_via_cdp_navigation, False),
        (_fetch_pdf_once, True),
    ):
        last_exc = None
        for attempt in range(1, _PDF_DOWNLOAD_RETRIES + 1):
            try:
                result = await _fetch(tab, url)
                if not result:
                    raise RuntimeError(f"empty response for {url}")
                body = base64.b64decode(result) if _decode else result
                _write_atomic(save_path, body)
                if check_magic:
                    _assert_pdf_magic(save_path, body, url)
                return {"size": len(body), "skipped": False, "reason": "downloaded", "saved_path": str(save_path)}
            except RuntimeError as exc:
                last_exc = exc
                if attempt < _PDF_DOWNLOAD_RETRIES:
                    await asyncio.sleep(_PDF_DOWNLOAD_RETRY_DELAY_S * attempt)
    raise last_exc


async def _try_curl_cffi(url, save_path, check_magic=True):
    """Fallback: download via curl_cffi with Chrome TLS impersonation.

    Returns a result dict with ``saved_path``. Raises ``RuntimeError``
    on failure. When ``check_magic`` is true the body must pass
    :func:`_assert_pdf_magic` (PDF path); supporting documents skip it.
    """
    try:
        from curl_cffi import AsyncSession
    except ImportError as exc:
        raise RuntimeError(f"curl_cffi not available for {url}") from exc
    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate="chrome", timeout=60.0)
    except Exception as exc:
        raise RuntimeError(f"curl_cffi request failed for {url}: {exc}") from exc
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
    if not r.content:
        raise RuntimeError(f"empty response for {url}")
    _write_atomic(save_path, r.content)
    if check_magic:
        _assert_pdf_magic(save_path, r.content, url)
    return {"size": len(r.content), "skipped": False, "reason": "downloaded", "saved_path": str(save_path)}


async def _download_browser(tab, url, save_path, check_magic):
    """Shared browser-fetch chain: CDP fetch, in-tab fetch, then curl_cffi fallback.

    Encapsulates the fallback order used by both the PDF and supporting
    document browser helpers. ``url`` is already HTTP->HTTPS upgraded by
    the caller. Returns the result dict of the winning strategy.

    When the 403 streak hits the limit, ``_track_download_outcome``
    pauses for the operator to clear Cloudflare. After the challenge
    clears, the full fetch chain retries with the fresh session.
    """
    last_exc = None
    for _cf_round in range(_CF_MAX_RETRIES + 1):
        try:
            try:
                result = await _try_browser_fetch(tab, url, save_path, check_magic)
            except RuntimeError:
                result = await _try_curl_cffi(url, save_path, check_magic)
        except RuntimeError as exc:
            last_exc = exc
            cleared = await _track_download_outcome(exc, tab=tab, url=url)
            if not cleared:
                if _is_http_403(exc) and _consecutive_403 < _BLOCK_STREAK_LIMIT:
                    await asyncio.sleep(_BLOCK_COOLDOWN_S)
                raise
            continue
        await _track_download_outcome(None, tab=tab, url=url)
        return result
    raise last_exc


async def download_pdf_browser(tab, url, save_path):
    """Download ``url`` into directory ``save_path``.

    The on-disk filename is a deterministic function of ``url``
    (``pdf_<sha1(url)[:12]>.pdf``), NOT the caller-supplied name —
    so re-runs in a different order produce the same path and the
    skip-by-path check stays correct.

    ``save_path`` is the downloads DIRECTORY (e.g. ``out_dir``).
    If a filename is passed instead, its parent directory is used.

    Primary: ``CDP Network.loadNetworkResource`` via
    :func:`_fetch_pdf_via_cdp_navigation`, which routes the request
    through Chrome's network stack at the browser-process level.  This
    bypasses renderer-enforced CORS/CSP and uses the real TLS
    fingerprint + cookies, so cross-origin and anti-bot-gated PDFs
    download without page-level fetch restrictions.

    Secondary: the browser's in-tab ``fetch()`` (:func:`_fetch_pdf_once`),
    which is faster for same-origin, non-gated resources and retries on
    transient failures.

    Fallback: ``curl_cffi`` with Chrome TLS impersonation when both
    browser paths fail (e.g. the page is closed or the CDP stream
    is unavailable).

    HTTP URLs are upgraded to HTTPS to avoid mixed-content blocking.

    Idempotent: when the target file already exists and is non-empty,
    the download is skipped (``skipped=True``).  Writes are atomic
    (temp + rename).

    Returns a dict with ``saved_path`` (the absolute path written)
    so the caller can store the exact ``pdf_filename`` in the DB:

        result = await download_pdf_browser(tab, file_url, out_dir)
        save_record(..., {"pdf_filename": Path(result["saved_path"]).name, ...})

    Raises ``RuntimeError`` if all three strategies fail.

    When ``_BLOCK_STREAK_LIMIT`` consecutive downloads are blocked with
    HTTP 403, the helper pauses for the operator to manually clear the
    Cloudflare challenge in the visible browser, then retries with
    fresh cookies. Re-running resumes via skip-existing.
    """
    url = _upgrade_to_https(_resolve_url(url, tab))
    save_dir = _resolve_download_dir(save_path)
    save_path = save_dir / _pdf_filename_for(url)
    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded", "saved_path": str(save_path)}

    await asyncio.sleep(random.uniform(_DOWNLOAD_DELAY_MIN_S, _DOWNLOAD_DELAY_MAX_S))
    return await _download_browser(tab, url, save_path, check_magic=True)


async def download_file_browser(tab, url, save_path):
    """Non-PDF variant of :func:`download_pdf_browser`; same browser-fetch chain.

    The on-disk name is ``doc_<sha1(canonical_url)[:12]><ext>``
    (``file_filename_for``) and the body is NOT validated as PDF
    (a supporting document must never trip the magic check).
    """
    url = _upgrade_to_https(_resolve_url(url, tab))
    save_dir = _resolve_download_dir(save_path)
    save_path = save_dir / file_filename_for(url)
    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded", "saved_path": str(save_path)}

    await asyncio.sleep(random.uniform(_DOWNLOAD_DELAY_MIN_S, _DOWNLOAD_DELAY_MAX_S))
    return await _download_browser(tab, url, save_path, check_magic=False)
