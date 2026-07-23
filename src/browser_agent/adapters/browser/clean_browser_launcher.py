"""Launch a clean Chromium instance and connect zendriver to it.

Zendriver's default ``zd.start()`` passes ~22 Chrome flags that
fingerprint the browser as automation (``--disable-features=...``,
``--no-first-run``, ``--password-store=basic``, etc.). Cloudflare
Turnstile and similar WAFs detect these at the process level.

This module launches Chromium directly with only the flags a real
user session would have — ``--remote-debugging-port`` (needed for CDP)
and ``--user-data-dir`` — then connects zendriver to the already-running
browser via ``zd.start(host=..., port=...)``.

Two consumers:

* :class:`ZendriverBrowserSession` — the persistent browser the agent
  drives during exploration.
* ``human_challenge.py`` — the standalone challenge probe.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import zendriver as zd
from loguru import logger

# ---------------------------------------------------------------------------
# Stealth JS injected on every document (same as ZendriverBrowserSession).
# ---------------------------------------------------------------------------
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

# Default Chromium binary. Override via env if needed.
_CHROMIUM_BIN = "/usr/bin/chromium"


def free_port() -> int:
    """Return an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def seed_profile_if_empty(profile_dir: Path) -> None:
    """Copy the real Chromium profile into ``profile_dir`` if it has no Cookies.

    A fresh profile looks like a brand-new browser to Cloudflare.
    Seeding it with the real profile's cookies and local state gives
    the browser a real-world fingerprint from the first run.
    Subsequent runs reuse the now-warm profile.
    """
    default_dir = profile_dir / "Default"
    if (default_dir / "Cookies").exists():
        return
    real_profile = Path.home() / ".config" / "chromium"
    if not (real_profile / "Default" / "Cookies").exists():
        logger.info("no real Chromium profile to seed from")
        return
    logger.info("seeding empty profile {} from real Chromium {}", profile_dir, real_profile)
    shutil.copytree(real_profile, profile_dir, dirs_exist_ok=True, symlinks=True)


def launch_chromium(
    port: int,
    user_data_dir: str | Path,
    headless: bool = False,
    user_agent: str | None = None,
) -> subprocess.Popen[bytes]:
    """Launch Chromium with minimal flags — only what a real user session has.

    Returns the subprocess handle. The caller is responsible for
    ``process.terminate()`` / ``process.kill()`` on shutdown.
    """
    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ]
    if headless:
        args.append("--headless=new")
    if user_agent:
        args.append(f"--user-agent={user_agent}")

    logger.info("launching clean Chromium: {}", " ".join(args))
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def connect_and_prepare(
    host: str = "127.0.0.1",
    port: int = 9222,
) -> tuple[zd.Browser, zd.Tab]:
    """Connect zendriver to a running Chromium and inject stealth JS.

    Returns ``(browser, tab)``. The tab has stealth JS registered on
    every new document via ``Page.addScriptToEvaluateOnNewDocument``.
    """
    browser = await zd.start(host=host, port=port)
    tab = browser.main_tab
    if tab is None:
        raise RuntimeError("zendriver connected but has no main tab")

    from zendriver.cdp import page

    await tab.send(page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))
    return browser, tab


def copy_profile(source: str | Path, prefix: str = "hc_profile_") -> Path:
    """Copy a Chrome profile to a temp directory so the original is not locked.

    Returns the path to the copy. The caller is responsible for
    ``shutil.rmtree`` on shutdown.
    """
    src = Path(source)
    dst = Path(tempfile.mkdtemp(prefix=prefix))
    logger.info("copying profile {} -> {}", src, dst)
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
    return dst


async def stop_browser(
    browser: zd.Browser,
    process: subprocess.Popen[bytes] | None,
    profile_dir: Path | None = None,
) -> None:
    """Stop the zendriver browser, kill the Chromium process, clean up profile."""
    try:
        await browser.stop()
    except Exception:
        logger.exception("failed to stop zendriver browser")

    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    if profile_dir is not None:
        shutil.rmtree(profile_dir, ignore_errors=True)
