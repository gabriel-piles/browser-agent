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
  uses ``tab.evaluate()`` + the browser's native ``fetch()`` to route
  the download through Chrome's real network stack, bypassing
  Cloudflare / Akamai anti-bot that blocks non-browser HTTP clients.

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


# ──────────────────────────────────────────────────────────────────
# curl_cffi strategy
# ──────────────────────────────────────────────────────────────────
# This block is intentionally a single literal string.  The
# in-process validation runner and the ``generate_script`` driver
# concatenate it in front of the LLM's emitted code so the script gets
# a real download helper without importing from this project.
EMITTED_CURL_CFFI_BLOCK = '''\
# ── BEGIN emitted curl_cffi pdf-download helper (vendored from browser_agent) ──
from pathlib import Path


async def download_pdf_curl_cffi(url, save_path, tab=None):
    """Download ``url`` to ``save_path`` via curl_cffi.

    Uses Chrome TLS fingerprint impersonation.  When ``tab`` is
    provided, cookies are extracted from the active browser session
    so cookie-gated / authenticated downloads work.

    Returns the file size in bytes.  Raises ``RuntimeError`` on
    failure (HTTP error, network error, empty response).
    """
    from curl_cffi import AsyncSession

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

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

    save_path.write_bytes(body)
    return len(body)
# ── END emitted curl_cffi pdf-download helper ──

'''

# ──────────────────────────────────────────────────────────────────
# browser_fetch strategy
# ──────────────────────────────────────────────────────────────────
EMITTED_BROWSER_FETCH_BLOCK = '''\
# ── BEGIN emitted browser-fetch pdf-download helper (vendored from browser_agent) ──
import base64
import json
from pathlib import Path

_PDF_DOWNLOAD_TIMEOUT_S = 90.0


async def download_pdf_browser(tab, url, save_path):
    """Download ``url`` to ``save_path`` via the browser's native ``fetch()``.

    Uses ``tab.evaluate()`` + the browser's ``fetch()`` from the current
    page context so the request goes through Chrome's real network stack
    (TLS fingerprint, headers, cookies, JS challenge clearance).  This
    bypasses Cloudflare / Akamai anti-bot that blocks non-browser HTTP
    clients.

    HTTP URLs are automatically upgraded to HTTPS to avoid mixed-content
    blocking when the current page is served over HTTPS.

    The tab MUST have already navigated to the target domain so the
    Cloudflare challenge is cleared before calling this function.

    Returns the file size in bytes.  Raises ``RuntimeError`` on failure.
    """
    import asyncio

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    if url.startswith("http://"):
        url = "https://" + url[7:]

    escaped = json.dumps(url)

    js = (
        f"(async () => {{\\n"
        f"  const r = await fetch({escaped}, {{ credentials: 'include' }});\\n"
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
        result = await asyncio.wait_for(
            tab.evaluate(js, await_promise=True),
            timeout=_PDF_DOWNLOAD_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"download timed out for {url}")

    if not result:
        raise RuntimeError(f"empty response for {url}")

    body = base64.b64decode(result)
    save_path.write_bytes(body)
    return len(body)
# ── END emitted browser-fetch pdf-download helper ──

'''
