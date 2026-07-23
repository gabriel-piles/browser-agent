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


# This block is intentionally a single literal string. The
# in-process validation runner and the ``generate_script`` driver
# concatenate it in front of the LLM's emitted code so the script gets a
# real HTML-capture helper without importing from this project.
EMITTED_SAVE_HTML_BLOCK = '''\
# ── BEGIN emitted save-page-html helper (vendored from browser_agent) ──
import hashlib
import os as _os
from pathlib import Path


def _html_filename_for(url):
    """Deterministic, collision-safe on-disk filename for ``url``.

    Returns ``html_<sha1(url)[:12]>.html`` — a pure function of the
    source URL, so "file exists at path" == "this exact page HTML was
    already saved" regardless of page order. Mirrors the PDF naming
    scheme so the two never collide (different prefix + extension).
    """
    return f"html_{hashlib.sha1(url.encode()).hexdigest()[:12]}.html"


def _html_existing_size(path):
    """Return existing on-disk size in bytes, or 0 when missing/empty."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return 0
    except OSError:
        return 0
    return st.st_size if st.st_size > 0 else 0


def _html_write_atomic(path, data):
    """Write ``data`` to ``path`` atomically (temp + rename). On any
    failure, remove the temp file. Renames are atomic on POSIX so a
    crash mid-write never leaves a partial file at ``path``. ``path``
    may be ``str`` or ``Path``."""
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(part, path)
    except Exception:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise


async def save_page_html(tab, save_path, source_url, filename=None):
    """Save the current page's HTML to ``save_path`` directory.

    Uses ``tab.get_content()`` to capture the full serialized DOM from
    the REAL browser tab — never an HTTP client (curl_cffi, requests,
    httpx, aiohttp). Sites behind Cloudflare / Akamai WAF would block a
    non-browser request and the HTML would be a challenge page, not the
    real content. The browser tab carries the same TLS fingerprint,
    cookies, and JS-challenge clearance as the PDF download path.

    ``save_path`` is the downloads DIRECTORY (e.g. ``out_dir``); the
    on-disk filename is ``html_<sha1(source_url)[:12]>.html`` —
    deterministic, collision-safe, same scheme as PDF naming. When
    ``filename`` is passed it is used instead (caller-supplied override).

    Idempotent: skips the write when the target file already exists and
    is non-empty. Writes are atomic (temp + rename).

    Returns a dict with ``saved_path`` (the absolute path written) so
    the caller can store the exact ``html_filename`` in the DB row:

        result = await save_page_html(tab, out_dir, page_url)
        save_record(..., {"html_filename": Path(result["saved_path"]).name, ...})
    """
    save_dir = Path(save_path)
    if not save_dir.is_dir():
        save_dir = save_dir.parent
    save_dir.mkdir(parents=True, exist_ok=True)
    name = filename if filename else _html_filename_for(source_url)
    save_path = save_dir / name

    existing = _html_existing_size(save_path)
    if existing > 0:
        return {"size": existing, "skipped": True, "reason": "already_saved",
                "saved_path": str(save_path)}

    try:
        html = await tab.get_content()
    except Exception:
        # Fallback: serialize the DOM via CDP evaluate. Same browser
        # tab, same session — still NOT an HTTP client.
        html = await tab.evaluate("document.documentElement.outerHTML")
    if not html:
        raise RuntimeError(f"empty HTML content for {source_url}")
    body = html.encode("utf-8")
    _html_write_atomic(save_path, body)
    return {"size": len(body), "skipped": False, "reason": "saved",
            "saved_path": str(save_path)}
# ── END emitted save-page-html helper ──

'''
