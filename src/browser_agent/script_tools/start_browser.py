"""Launch a clean Chromium and connect zendriver. Replaces ``zd.start()``.

Moved verbatim from ``browser_agent.adapters.emitted_clean_launch``.
Launches Chromium with only ``--remote-debugging-port`` and
``--user-data-dir``, injects stealth JS, and patches ``browser.stop()``
to also kill the Chromium process on cleanup. The NopeCHA extension dir
and profile path come from ``script_tools.run_config`` (stamped per run
by the copier).

Robustness notes (added after a recurring "Failed to connect to
browser" traceback when re-running emitted scripts):

* Chromium's stderr is captured (not discarded) so a startup failure
  surfaces a real reason instead of zendriver's generic
  ``no_sandbox=True`` hint.
* We wait for the debugging port to accept a connection before calling
  ``zd.start``; if Chromium exits first, we raise its stderr.
* Stale ``SingletonLock`` / ``SingletonCookie`` / ``SingletonSocket``
  entries left behind by a crashed or Ctrl-C'd previous run are cleared
  when their owner PID is dead, so re-running a script does not collide
  with an orphaned Chromium holding the shared ``<run>/profile`` lock.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
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

# Bounded wait for Chromium's --remote-debugging-port to accept connections.
_STARTUP_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.1
# Chromium lockfiles in the user-data-dir; a stale lock blocks the next launch.
_LOCKFILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


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


def _clear_stale_locks(profile_dir):
    """Remove Chromium singleton lockfiles whose owner process is dead.

    A live Chromium holds ``SingletonLock`` (a symlink to ``hostname-PID``).
    If that PID no longer exists, the lock is stale — left behind by a
    crashed or killed previous run — and the next launch would refuse
    the profile with "Profile directory is in use". Live locks are left
    untouched so we never steal an in-use profile from a running Chromium.
    """
    for name in _LOCKFILES:
        path = Path(profile_dir) / name
        if not path.exists() and not path.is_symlink():
            continue
        if _lock_owner_alive(path):
            continue
        _safe_remove(path)


def _lock_owner_alive(path):
    """Return True if ``SingletonLock`` points at a currently-running PID."""
    if not path.is_symlink():
        return True
    try:
        target = os.readlink(path)
    except OSError:
        return True
    pid = _parse_pid(target)
    if pid is None:
        return True
    return _pid_alive(pid)


def _parse_pid(target):
    """Extract the trailing ``-<pid>`` from a Chromium SingletonLock target."""
    tail = target.rsplit("-", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None


def _pid_alive(pid):
    """Return True if ``pid`` is currently running on this host."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _safe_remove(path):
    """Remove a file or symlink, ignoring missing-path errors."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _build_chromium_args(port, profile, headless):
    """Compose the minimal-flag Chromium argv (only what a real session has)."""
    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
    ]
    if headless:
        args.append("--headless=new")
    if NOPECHA_EXTENSION_DIR:
        args.append(f"--load-extension={NOPECHA_EXTENSION_DIR}")
    return args


def _launch_chromium(args):
    """Start Chromium with stderr captured so startup failures are visible."""
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _port_open(host, port):
    """Return True if a TCP connection to ``host:port`` succeeds right now."""
    try:
        with socket.create_connection((host, port), timeout=_POLL_INTERVAL_S):
            return True
    except OSError:
        return False


def _wait_for_port(process, port, timeout_s):
    """Block until ``port`` accepts connections or ``process`` exits.

    Raises ``RuntimeError`` carrying Chromium's stderr if the process
    dies before the port opens, or if ``timeout_s`` elapses (in which
    case the process is terminated first so stderr reaches EOF).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise _chromium_died_error(process)
        if _port_open("127.0.0.1", port):
            return
        time.sleep(_POLL_INTERVAL_S)
    _terminate(process)
    raise _chromium_died_error(process, timed_out=True)


def _terminate(process):
    """Terminate Chromium and wait briefly so its stderr pipe reaches EOF."""
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _chromium_died_error(process, timed_out=False):
    """Build a ``RuntimeError`` that carries Chromium's captured stderr."""
    stderr = _read_stderr(process)
    reason = "timed out" if timed_out else f"exited with code {process.returncode}"
    return RuntimeError(f"Chromium {reason} before opening the debugging port.\nstderr:\n{stderr}")


def _read_stderr(process):
    """Drain and decode Chromium's stderr pipe, truncated for log safety."""
    if process.stderr is None:
        return "<no stderr pipe>"
    try:
        raw = process.stderr.read()
    except Exception:
        return "<unreadable stderr>"
    text = raw.decode("utf-8", errors="replace").strip()
    return text or "<empty stderr>"


def _close_stderr(process):
    """Close the captured stderr pipe so the file descriptor does not leak."""
    if process.stderr is not None:
        try:
            process.stderr.close()
        except OSError:
            pass


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
    _clear_stale_locks(profile)

    process = _launch_chromium(_build_chromium_args(port, profile, headless))
    _wait_for_port(process, port, _STARTUP_TIMEOUT_S)

    browser = await zd.start(host="127.0.0.1", port=port)
    tab = browser.main_tab

    # Inject stealth JS on every document.
    await tab.send(zd.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_JS))

    _original_stop = browser.stop

    async def _clean_stop():
        await _original_stop()
        _terminate(process)
        _close_stderr(process)
        if owns_profile:
            shutil.rmtree(profile, ignore_errors=True)

    browser.stop = _clean_stop
    return browser
