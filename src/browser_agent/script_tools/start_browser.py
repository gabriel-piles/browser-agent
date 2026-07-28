"""Launch a clean Chromium and connect zendriver. Replaces ``zd.start()``.

Moved verbatim from ``browser_agent.adapters.emitted_clean_launch``.
Launches Chromium with only ``--remote-debugging-port`` and
``--user-data-dir``, injects stealth JS, and patches ``browser.stop()``
to also kill the Chromium process on cleanup. The NopeCHA extension dir
and profile path come from ``script_tools.run_config`` (stamped per run
by the copier).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import zendriver as zd

from script_tools.run_config import NOPECHA_EXTENSION_DIR, PROFILE_PATH

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

# Same default the agent uses (see ``configuration.ZENDRIVER_HEADLESS``).
_EMITTED_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
_CHROMIUM_BIN = "/usr/bin/chromium"
_REAL_CHROMIUM_PROFILE = Path.home() / ".config" / "chromium"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _seed_profile_if_empty(profile_dir):
    """Copy the real Chromium profile in when ``profile_dir`` has no Cookies.

    A fresh profile looks like a brand-new browser to Cloudflare.
    Seeding it with the real profile's cookies and local state gives
    the browser a real-world fingerprint from the first run.
    Subsequent runs reuse the now-warm profile. Mirrors
    :meth:`ZendriverBrowserSession._seed_profile_if_empty`.
    """
    default_dir = Path(profile_dir) / "Default"
    if (default_dir / "Cookies").exists():
        return
    # Try Chromium profile first, then Google Chrome as fallback.
    real_profile = _REAL_CHROMIUM_PROFILE
    if not (real_profile / "Default" / "Cookies").exists():
        alt_profile = Path.home() / ".config" / "google-chrome"
        if (alt_profile / "Default" / "Cookies").exists():
            real_profile = alt_profile
        else:
            return
    shutil.copytree(real_profile, profile_dir, dirs_exist_ok=True, symlinks=True)


async def start_browser(headless=None, user_data_dir=None):
    """Launch a clean Chromium and connect zendriver. Replaces ``zd.start()``.

    Mirrors :class:`ZendriverBrowserSession` so the final emitted
    script's browser fingerprint matches the agent's exploration
    browser:

    * Chromium is launched with only ``--remote-debugging-port`` and
      ``--user-data-dir`` — no automation-flagging arguments.
    * The real Chromium profile is always copied into the profile
      directory when it is empty (seeding from ``~/.config/chromium``
      or ``~/.config/google-chrome``) — even for auto-created temp
      profiles — so the script's browser fingerprint matches a real
      user's installation from the first run.
    * ``headless`` defaults to the ``ZENDRIVER_HEADLESS`` env var
    * The returned ``zd.Browser``'s ``.stop()`` is patched to also
      kill the Chromium process and clean up the auto-created
      profile on shutdown.

    The ``user_agent`` argument is intentionally omitted: the agent
    does not expose it and the underlying launch does not support
    it.
    """
    if headless is None:
        headless = _EMITTED_HEADLESS

    port = _free_port()
    owns_profile = user_data_dir is None and not PROFILE_PATH
    profile = user_data_dir or PROFILE_PATH or tempfile.mkdtemp(prefix="zd_script_")

    Path(profile).mkdir(parents=True, exist_ok=True)
    _seed_profile_if_empty(profile)

    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
    ]
    if headless:
        args.append("--headless=new")
    if NOPECHA_EXTENSION_DIR:
        args.append(f"--load-extension={NOPECHA_EXTENSION_DIR}")

    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    browser = await zd.start(host="127.0.0.1", port=port)
    tab = browser.main_tab

    # Inject stealth JS on every document.
    await tab.send(zd.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))

    _original_stop = browser.stop

    async def _clean_stop():
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
