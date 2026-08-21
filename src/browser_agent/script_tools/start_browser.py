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
* A persistent profile (the run's ``<run>/profile``) is deleted and
  re-created from the real Chromium profile before every launch, so a
  crashed or Ctrl-C'd previous run's stale ``SingletonLock``/corrupt
  store can never block the next launch or hand it off to an orphaned
  Chromium holding the shared profile lock.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
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
_CHROMIUM_NO_SANDBOX = os.environ.get("CHROMIUM_NO_SANDBOX", "").lower() in {"1", "true", "yes"} or os.geteuid() == 0

# Bounded wait for Chromium's --remote-debugging-port to accept connections.
_STARTUP_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.1

# Chromium singleton lockfiles. Never copied when seeding a profile: if the
# real Chromium is running, its live lock would make the seeded browser
# refuse the directory and never open the debugging port.
_LOCKFILES = frozenset({"SingletonLock", "SingletonSocket", "SingletonCookie"})


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _seed_profile_if_empty(profile_dir):
    """Copy the real Chromium profile in when ``profile_dir`` has no Cookies.

    A fresh profile looks like a brand-new browser to Cloudflare.
    Seeding it with the real profile's cookies and local state gives
    the browser a real-world fingerprint from the first run. Singleton
    lockfiles are never copied so a running Chromium's live lock cannot
    poison the seed.
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
    shutil.copytree(
        real_profile,
        profile_dir,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=lambda _dir, names: _LOCKFILES & set(names),
    )


def _reset_profile(profile_dir):
    """Wipe a persistent profile and re-create it seeded from the real one.

    Reusing a run's profile across launches accumulates stale singleton
    locks and possibly-corrupt stores; the next Chromium then refuses
    the directory ("Profile in use") or hands off to a still-running
    prior instance and never opens its debug port. Start every browser
    from a freshly seeded directory instead.
    """
    shutil.rmtree(profile_dir, ignore_errors=True)
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    _seed_profile_if_empty(profile_dir)


def _build_chromium_args(port, profile, headless):
    """Compose the minimal-flag Chromium argv (only what a real session has)."""
    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
    ]
    if _CHROMIUM_NO_SANDBOX:
        args.append("--no-sandbox")
    if headless:
        args.append("--headless=new")
    if NOPECHA_EXTENSION_DIR:
        args.append(f"--load-extension={NOPECHA_EXTENSION_DIR}")
    return args


def _launch_chromium(args):
    """Start Chromium with stderr captured so startup failures are visible.

    ``start_new_session`` puts Chromium and its forked children in one
    process group so ``_terminate`` can kill the whole tree — children
    inherit the stderr pipe, so killing only the main process would leave
    them holding the write end and block any later stderr read.
    """
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
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
    """TERM (then KILL) the whole Chromium process group.

    Chromium forks zygote/gpu/renderer children that inherit the stderr
    pipe; terminating only the main process leaves them holding the
    write end. ``_launch_chromium`` starts Chromium in its own session,
    so killing the process group reaps the whole tree and closes the
    pipe (stderr reads then reach EOF).
    """
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait(timeout=5)


def _chromium_died_error(process, timed_out=False):
    """Build a ``RuntimeError`` that carries Chromium's captured stderr."""
    stderr = _read_stderr(process)
    reason = "timed out" if timed_out else f"exited with code {process.returncode}"
    return RuntimeError(f"Chromium {reason} before opening the debugging port.\nstderr:\n{stderr}")


def _read_stderr(process):
    """Boundedly drain Chromium's stderr pipe, truncated for log safety.

    Never blocks indefinitely: Chromium's forked children inherit the
    pipe, so a plain ``read()`` would wait for every child to die. A
    select timeout caps the wait and returns whatever stderr arrived.
    """
    if process.stderr is None:
        return "<no stderr pipe>"
    fd = process.stderr.fileno()
    chunks = []
    deadline = time.monotonic() + 2.0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except (OSError, ValueError):
        pass
    text = b"".join(chunks).decode("utf-8", errors="replace").strip()
    return text or "<empty stderr>"


def _close_stderr(process):
    """Close the captured stderr pipe so the file descriptor does not leak."""
    if process.stderr is not None:
        try:
            process.stderr.close()
        except OSError:
            pass


def _chromium_pgids_under(root_dir):
    """Distinct process-group ids for live Chromium using a profile under ``root_dir``.

    Matches the process name containing ``chrom`` with a resolved
    ``--user-data-dir`` relative to ``root_dir``, so the operator's real
    browser under ``~/.config`` is never touched.
    """
    root = str(Path(root_dir).resolve())
    groups = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return groups
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == os.getpid():
            continue
        try:
            args = Path("/proc", entry, "cmdline").read_bytes().split(b"\x00")
        except OSError:
            continue
        if not any(b"chrom" in a for a in args):
            continue
        ud = next(
            (a.split(b"=", 1)[1].decode("utf-8", errors="replace") for a in args if a.startswith(b"--user-data-dir=")),
            None,
        )
        if ud is None or not Path(ud).resolve().is_relative_to(root):
            continue
        try:
            groups.add(os.getpgid(pid))
        except OSError:
            continue
    return groups


def _terminate_group(pgid):
    """TERM then KILL one process group, waiting ~2s for it to exit."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            break
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_chromium_under(root_dir):
    """Reap every live Chromium group whose profile dir is under ``root_dir``."""
    for pgid in sorted(_chromium_pgids_under(root_dir)):
        _terminate_group(pgid)


async def start_browser(headless=None, user_data_dir=None):
    """Launch a clean Chromium and connect zendriver. Replaces ``zd.start()``.

    Mirrors :class:`ZendriverBrowserSession` so the final emitted
    script's browser fingerprint matches the agent's exploration
    browser:

    * Chromium is launched with only ``--remote-debugging-port`` and
      ``--user-data-dir`` — no automation-flagging arguments.
    * The real Chromium profile is always copied into the profile
      directory (seeding from ``~/.config/chromium`` or
      ``~/.config/google-chrome``) — even for auto-created temp
      profiles — so the script's browser fingerprint matches a real
      user's installation from the first run. A persistent profile is
      deleted and re-seeded on every launch (see ``_reset_profile``).
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

    owns_profile = user_data_dir is None and not PROFILE_PATH
    profile = user_data_dir or PROFILE_PATH or tempfile.mkdtemp(prefix="zd_script_")

    # Reap any still-open Chromium from an interrupted previous run of this
    # script so this launch opens its own debug port instead of handing off
    # to the stale instance holding the profile lock.
    _kill_chromium_under(profile)

    if owns_profile:
        # Fresh temp directory — just seed it.
        Path(profile).mkdir(parents=True, exist_ok=True)
        _seed_profile_if_empty(profile)
    else:
        # Persistent profile — wipe and re-create seeded on every launch so
        # stale locks/corrupt state from a prior run cannot block this one.
        _reset_profile(profile)

    # Retry port allocation: _free_port() has a TOC/TOU race when multiple
    # scripts start concurrently; retry with a fresh port if the first
    # attempt fails.
    _MAX_PORT_RETRIES = 5
    for attempt in range(_MAX_PORT_RETRIES):
        port = _free_port()
        process = _launch_chromium(_build_chromium_args(port, profile, headless))
        try:
            _wait_for_port(process, port, _STARTUP_TIMEOUT_S)
        except RuntimeError as exc:
            _terminate(process)
            _close_stderr(process)
            if attempt < _MAX_PORT_RETRIES - 1:
                continue
            raise
        break

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
