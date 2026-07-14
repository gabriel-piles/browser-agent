"""Self-contained clean-launch helper inlined into the final emitted script.

The final script the operator runs from ``data/scripts/`` is
self-contained by contract — it MUST NOT import from this project.
The LLM's emitted code currently uses ``zd.start()`` which passes
~22 automation-flagging Chrome arguments that Cloudflare Turnstile
detects.

This helper is shipped as a plain-Python string and prepended to the
final ``python_code`` in :mod:`browser_agent.drivers.generate_script`.
It provides a ``start_browser()`` function that replaces
``zd.start()``: launches Chromium with only ``--remote-debugging-port``
and ``--user-data-dir``, connects zendriver, injects stealth JS, and
patches ``browser.stop()`` to also kill the Chromium process on
cleanup. In-process validation does NOT use this helper — it shares
the agent's already-running browser.

The vendored helper is a near-mirror of
:class:`ZendriverBrowserSession`'s launch path
(:mod:`browser_agent.adapters.browser.zendriver_browser_session`) so
the final script and the agent's exploration browser look identical
to anti-bot systems:

* same ``--remote-debugging-port`` / ``--user-data-dir`` only flags
  (no ``--disable-features``, ``--no-first-run``, etc.),
* same stealth JS injected via ``Page.addScriptToEvaluateOnNewDocument``,
* same real-profile seeding: a fresh profile looks like a brand-new
  browser to Cloudflare. The helper always seeds an empty profile
  from the user's real Chromium or Google Chrome profile
  (``~/.config/chromium`` or ``~/.config/google-chrome``) regardless
  of whether ``user_data_dir`` was provided, so the fingerprint
  matches a real user's installation from the first run. Mirrors
  :meth:`ZendriverBrowserSession._seed_profile_if_empty`.
"""

from __future__ import annotations

import re

# Match a call to zendriver's start() that the LLM emitted. Captures
# any preceding ``await`` / argument list (including multiline). The
# first alternative covers ``import zendriver as zd; zd.start(...)``,
# the second covers ``import zendriver; zendriver.start(...)`` and
# any other alias. Whitespace before the call is preserved.
_EMITTED_ZD_START_RE = re.compile(
    r"(?P<head>(?:await\s+)?)(?P<callee>\bzd\.start\b|\bzendriver\.start\b)"
    r"(?P<args>\s*\([^()]*?(?:\([^()]*\)[^()]*)*\))",
    re.DOTALL,
)


def with_emitted_normalize_launch(python_code: str) -> str:
    """Rewrite ``zd.start(...)`` calls in the LLM's emitted code.

    The vendored helper provides ``start_browser()`` which mirrors
    the agent's :class:`ZendriverBrowserSession` launch path
    (minimal Chrome flags, real-profile seeding, stealth JS,
    ``ZENDRIVER_HEADLESS`` env). The LLM sometimes emits
    ``await zd.start(headless=False)`` instead, which passes
    ~22 automation-flagging Chrome arguments and triggers anti-bot
    checks. The in-process validation runner shims
    ``zendriver.start`` to share the agent's tab, so validation
    succeeds even with ``zd.start()`` — the bug only surfaces when
    the operator runs the final emitted script.

    This normalizer runs on the LLM's code BEFORE the vendored
    helper is prepended, so it never touches the helper itself.
    It rewrites every match to a call to the (then-vendored)
    ``start_browser()``, dropping arguments the agent does not
    expose (``user_agent``) and passing through the rest unchanged.
    """
    rewritten = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal rewritten
        rewritten += 1
        head = match.group("head")
        args_text = match.group("args")
        # Strip arguments the agent's start_browser does not accept.
        # ``headless`` is supported; ``user_data_dir`` is supported;
        # ``user_agent`` is not (the launch path does not forward it).
        cleaned = _strip_user_agent_kwarg(args_text)
        return f"{head}start_browser{cleaned}"

    normalized = _EMITTED_ZD_START_RE.sub(_replace, python_code)
    if rewritten:
        from loguru import logger

        logger.info(
            "emitted-script normalizer rewrote {n} zd.start() call(s) to start_browser()",
            n=rewritten,
        )
    return normalized


def _strip_user_agent_kwarg(args_text: str) -> str:
    """Remove ``user_agent=...`` from an argument list, comma-safe.

    Preserves any whitespace inside the parens. If the cleaned
    argument list becomes empty (or just whitespace), returns the
    original text with parens preserved (so ``zd.start()`` becomes
    ``start_browser()``).
    """
    # Match ``user_agent=<value>`` where <value> is a balanced
    # expression with no top-level commas. String-literal
    # alternatives are listed FIRST so the negation fallback
    # (``[^,()[\]{}]``) does not silently consume a single quote
    # character and truncate the value mid-string.
    pattern = re.compile(
        r",?\s*user_agent\s*=\s*"
        r"(?:'[^'\\]*(?:\\.[^'\\]*)*'"
        r"|\"[^\"\\]*(?:\\.[^\"\\]*)*\""
        r"|\([^)]*\)"
        r"|\[[^\]]*\]"
        r"|\{[^{}]*\}"
        r"|[^,()[\]{}])",
        re.DOTALL,
    )
    cleaned = pattern.sub("", args_text)
    # Tidy up a leading comma with no preceding argument.
    cleaned = re.sub(r"\(\s*,", "(", cleaned)
    return cleaned or "()"


def with_emitted_inject_profile_path(python_code: str, profile_path: str) -> str:
    """Inject ``user_data_dir`` into every ``start_browser(...)`` call.

    Runs after ``with_emitted_normalize_launch`` so all calls are
    already ``start_browser(...)``.  If the call already has a
    ``user_data_dir`` kwarg it is left unchanged; otherwise the agent's
    exploration profile path is injected so the emitted script reuses
    the same warm profile (with cookies / clearance tokens that the
    agent built up during exploration).
    """
    _START_BROWSER_RE = re.compile(
        r"(?P<head>(?:await\s+)?)(?P<callee>\bstart_browser\b)" r"(?P<args>\s*\([^()]*?(?:\([^()]*\)[^()]*)*\))",
        re.DOTALL,
    )
    _HAS_USER_DATA_DIR_RE = re.compile(r"\buser_data_dir\s*=")
    rewritten = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal rewritten
        head = match.group("head")
        args_text = match.group("args")
        if _HAS_USER_DATA_DIR_RE.search(args_text):
            return match.group(0)
        # Insert user_data_dir as the first argument.
        if args_text.strip() == "()":
            new_args = f"(user_data_dir={profile_path!r})"
        else:
            inner = args_text[1:-1].strip()
            new_args = f"(user_data_dir={profile_path!r}, {inner})"
        rewritten += 1
        return f"{head}start_browser{new_args}"

    normalized = _START_BROWSER_RE.sub(_replace, python_code)
    if rewritten:
        from loguru import logger

        logger.info(
            "emitted-script injector wrote user_data_dir into {n} start_browser() call(s) " "pointing at {path}",
            n=rewritten,
            path=profile_path,
        )
    return normalized


def with_emitted_clean_launch(python_code: str) -> str:
    """Prepend the vendored clean-launch helper to ``python_code``.

    Only the final-script emit path (:mod:`generate_script._emit`)
    calls this so the helper appears at the top of every script the
    operator runs. The helper is idempotent: if the script already
    contains the block marker it is returned unchanged.
    """
    if "BEGIN emitted clean-launch helper" in python_code:
        return python_code
    return f"{EMITTED_CLEAN_LAUNCH_BLOCK}{python_code}"


EMITTED_CLEAN_LAUNCH_BLOCK = '''\
# ── BEGIN emitted clean-launch helper (vendored from browser_agent) ──
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import zendriver as zd

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
    owns_profile = user_data_dir is None
    profile = user_data_dir or tempfile.mkdtemp(prefix="zd_script_")

    Path(profile).mkdir(parents=True, exist_ok=True)
    _seed_profile_if_empty(profile)

    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
    ]
    if headless:
        args.append("--headless=new")

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
# ── END emitted clean-launch helper ──

'''
