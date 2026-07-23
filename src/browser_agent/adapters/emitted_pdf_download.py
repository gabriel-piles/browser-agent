"""Self-contained PDF download helpers inlined into every emitted script.

The final script the operator runs from ``data/runs/<run>/scripts/``
is self-contained by contract and MUST NOT import from this project.

Two strategies are supported:

* **curl_cffi** — ``download_pdf_curl_cffi(url, save_path, tab=None)``
  uses ``curl_cffi.AsyncSession`` with Chrome TLS impersonation.  When
  a ``tab`` is passed, cookies are extracted from the active browser
  session so cookie-gated downloads work.  Faster and simpler; works
  for sites without JS-challenge anti-bot protection.

* **browser_fetch** — ``download_pdf_browser(tab, url, save_path)``
  tries, in order: a CDP ``Network.loadNetworkResource`` fetch
  (:func:`_fetch_pdf_via_cdp_navigation`), which routes through
  Chrome's network stack at the browser-process level and bypasses
  renderer-enforced CORS/CSP; the in-tab ``fetch()``, which is faster
  for same-origin, non-gated resources; and ``curl_cffi`` as the final
  fallback.  Used for sites behind Cloudflare/Akamai WAF or where
  cross-origin downloads are blocked.

The agent determines which strategy to use by calling the
``download_pdf`` tool (which tries curl_cffi).  If curl_cffi succeeds,
``pdf_download_strategy="curl_cffi"``; if it fails (HTTP 403 etc.),
``pdf_download_strategy="browser_fetch"``.  The final emitted script
only includes the helper for the chosen strategy.

Both the in-process validation runner and the operator-run final script
get the helper — see
:func:`browser_agent.drivers.generate_script._emit` and
:meth:`InProcessScriptRunnerAdapter.run`.
"""

from __future__ import annotations

from browser_agent.adapters.emitted_snippets import (
    ATOMIC_WRITE_SNIPPET,
    EXISTING_SIZE_SNIPPET,
    PDF_FILENAME_SNIPPET,
)

_SHARED_HELPERS = (
    "import hashlib\n"
    "import os as _os\n"
    "from pathlib import Path\n"
    "\n\n"
    f"{PDF_FILENAME_SNIPPET}\n\n"
    f"{EXISTING_SIZE_SNIPPET}\n\n"
    f"{ATOMIC_WRITE_SNIPPET}"
)

_CURL_CFFI_DOWNLOAD = '''\


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

        result = await download_pdf_curl_cffi(pdf_url, out_dir, tab)
        save_record(..., {"pdf_filename": Path(result["saved_path"]).name, ...})

    Raises ``RuntimeError`` on failure (HTTP error, network error,
    empty response).
    """
    from curl_cffi import AsyncSession

    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / _pdf_filename_for(url)

    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded",
                "saved_path": str(save_path)}

    cookies = {}
    if tab is not None:
        try:
            from zendriver.cdp import network as _net
            cdp_cookies = await tab.send(_net.get_cookies([url]))
            cookies = {c.name: c.value for c in cdp_cookies
                       if getattr(c, "name", None) and getattr(c, "value", None)}
        except Exception:
            pass

    try:
        async with AsyncSession() as s:
            r = await s.get(url, impersonate="chrome",
                            cookies=cookies, timeout=60.0)
    except Exception as e:
        raise RuntimeError(f"curl_cffi request failed for {url}: {e}")

    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")

    body = r.content
    if not body:
        raise RuntimeError(f"empty response for {url}")

    _write_atomic(save_path, body)
    return {"size": len(body), "skipped": False, "reason": "downloaded",
            "saved_path": str(save_path)}'''


EMITTED_CURL_CFFI_BLOCK = (
    "# ── BEGIN emitted curl_cffi pdf-download helper (vendored from browser_agent) ──\n"
    f"{_SHARED_HELPERS}"
    f"{_CURL_CFFI_DOWNLOAD}\n"
    "# ── END emitted curl_cffi pdf-download helper ──\n\n"
)

_BROWSER_FETCH_HELPERS = """\
import asyncio
import base64
import hashlib
import json
import os as _os
from pathlib import Path

_PDF_DOWNLOAD_TIMEOUT_S = 90.0
_PDF_DOWNLOAD_RETRIES = 3
_PDF_DOWNLOAD_RETRY_DELAY_S = 1.5"""


_BROWSER_FETCH_CDP = '''\


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
    res = await tab.send(_net.load_network_resource(
        url=url,
        options=_net.LoadNetworkResourceOptions(
            disable_cache=True,
            include_credentials=True,
        ),
        frame_id=frame_id,
    ))
    if not res.success:
        raise RuntimeError(
            f"CDP fetch failed net_error={res.net_error} "
            f"({res.net_error_name}) for {url}"
        )
    if res.http_status_code and res.http_status_code >= 400:
        raise RuntimeError(f"HTTP {int(res.http_status_code)} for {url}")
    handle = res.stream
    if handle is None:
        raise RuntimeError(f"no stream handle for {url}")
    chunks = []
    offset = None
    while True:
        b64, data, eof = await tab.send(_io.read(handle, offset=offset, size=None))
        if data:
            chunks.append(base64.b64decode(data) if b64 else data.encode())
        if eof:
            break
    body = b"".join(chunks)
    if not body:
        raise RuntimeError(f"empty CDP stream for {url}")
    return body'''


_BROWSER_FETCH_TAB = '''\


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
        raise RuntimeError(f"fetch failed for {url}: {exc}") from exc'''


_BROWSER_FETCH_TRY = '''\


async def _try_browser_fetch(tab, url, save_path):
    """Try CDP-bypass fetch, then in-tab fetch; write ``save_path`` atomically.

    The CDP path (:func:`_fetch_pdf_via_cdp_navigation`) is CORS/CSP-proof
    and uses the real TLS fingerprint + cookies, so it is tried first.
    The in-tab ``fetch()`` (:func:`_fetch_pdf_once`) is faster for
    same-origin, non-gated resources and is tried second.  Each path is
    retried up to ``_PDF_DOWNLOAD_RETRIES`` times.  Returns a result
    dict with ``saved_path``; raises ``RuntimeError`` on final failure.
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
                return {"size": len(body), "skipped": False,
                        "reason": "downloaded", "saved_path": str(save_path)}
            except RuntimeError as exc:
                last_exc = exc
                if attempt < _PDF_DOWNLOAD_RETRIES:
                    await asyncio.sleep(_PDF_DOWNLOAD_RETRY_DELAY_S * attempt)
    raise last_exc'''


_BROWSER_FETCH_CURL_FALLBACK = '''\


async def _try_curl_cffi(url, save_path):
    """Fallback: download via curl_cffi with Chrome TLS impersonation.

    Returns a result dict with ``saved_path``. Raises ``RuntimeError``
    on failure.
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
    return {"size": len(r.content), "skipped": False, "reason": "downloaded",
            "saved_path": str(save_path)}'''


_BROWSER_FETCH_MAIN = '''\


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
    browser paths fail (e.g. the page is closed or the CDP stream is
    unavailable).

    HTTP URLs are upgraded to HTTPS to avoid mixed-content blocking.

    Idempotent: when the target file already exists and is non-empty,
    the download is skipped (``skipped=True``).  Writes are atomic
    (temp + rename).

    Returns a dict with ``saved_path`` (the absolute path written)
    so the caller can store the exact ``pdf_filename`` in the DB:

        result = await download_pdf_browser(tab, pdf_url, out_dir)
        save_record(..., {"pdf_filename": Path(result["saved_path"]).name, ...})

    Raises ``RuntimeError`` if all three strategies fail.
    """
    if url.startswith("http://"):
        url = "https://" + url[7:]
    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / _pdf_filename_for(url)
    existing = _existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_downloaded",
                "saved_path": str(save_path)}
    try:
        return await _try_browser_fetch(tab, url, save_path)
    except RuntimeError:
        pass
    return await _try_curl_cffi(url, save_path)'''


EMITTED_BROWSER_FETCH_BLOCK = (
    "# ── BEGIN emitted browser-fetch pdf-download helper (vendored from browser_agent) ──\n"
    f"{_BROWSER_FETCH_HELPERS}\n\n"
    f"{PDF_FILENAME_SNIPPET}\n\n"
    f"{EXISTING_SIZE_SNIPPET}\n\n"
    f"{ATOMIC_WRITE_SNIPPET}"
    f"{_BROWSER_FETCH_CDP}"
    f"{_BROWSER_FETCH_TAB}"
    f"{_BROWSER_FETCH_TRY}"
    f"{_BROWSER_FETCH_CURL_FALLBACK}"
    f"{_BROWSER_FETCH_MAIN}\n"
    "# ── END emitted browser-fetch pdf-download helper ──\n\n"
)


def with_emitted_pdf_download(
    python_code: str,
    strategy: str = "browser_fetch",
) -> str:
    """Prepend the vendored pdf-download helper to ``python_code``.

    ``strategy`` is either ``"curl_cffi"`` or ``"browser_fetch"``.
    The matching helper block is prepended.  Idempotent: if the
    script already contains the block marker it is returned
    unchanged.
    """
    if strategy == "curl_cffi":
        if "BEGIN emitted curl_cffi pdf-download helper" in python_code:
            return python_code
        return f"{EMITTED_CURL_CFFI_BLOCK}{python_code}"
    # Default: browser_fetch
    if "BEGIN emitted browser-fetch pdf-download helper" in python_code:
        return python_code
    return f"{EMITTED_BROWSER_FETCH_BLOCK}{python_code}"


def with_emitted_all_pdf_downloads(python_code: str) -> str:
    """Prepend BOTH pdf-download helpers to ``python_code``.

    Used by the in-process validation runner so the LLM's validation
    script can test either strategy before deciding which one the
    final script should use.
    """
    if "BEGIN emitted curl_cffi pdf-download helper" not in python_code:
        python_code = f"{EMITTED_CURL_CFFI_BLOCK}{python_code}"
    if "BEGIN emitted browser-fetch pdf-download helper" not in python_code:
        python_code = f"{EMITTED_BROWSER_FETCH_BLOCK}{python_code}"
    return python_code
