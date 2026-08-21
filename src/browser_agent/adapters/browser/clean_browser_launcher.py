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
from loguru import logger

from browser_agent.configuration import CHROMIUM_NO_SANDBOX, CHROMIUM_WINDOW_POSITION

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

# Bounded wait for Chromium's --remote-debugging-port to accept connections.
_CHROMIUM_STARTUP_TIMEOUT_S = 10.0
_PORT_POLL_INTERVAL_S = 0.1

# Chromium singleton lockfiles. Never copied when seeding a profile: if the
# real Chromium is running, its Live lock would make the seeded browser refuse
# the directory ("Profile in use") and never open the debugging port.
_SINGLETON_LOCKFILES = frozenset({"SingletonLock", "SingletonSocket", "SingletonCookie"})


def free_port() -> int:
    """Return an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def seed_profile_if_empty(profile_dir: Path) -> None:
    """Copy the real Chromium profile into ``profile_dir`` if it has no Cookies.

    A fresh profile looks like a brand-new browser to Cloudflare.
    Seeding it with the real profile's cookies and local state gives
    the browser a real-world fingerprint from the first run. Singleton
    lockfiles are never copied so a running Chromium's live lock cannot
    poison the seed.
    """
    default_dir = profile_dir / "Default"
    if (default_dir / "Cookies").exists():
        return
    real_profile = Path.home() / ".config" / "chromium"
    if not (real_profile / "Default" / "Cookies").exists():
        logger.info("no real Chromium profile to seed from")
        return
    logger.info("seeding empty profile {} from real Chromium {}", profile_dir, real_profile)
    shutil.copytree(
        real_profile,
        profile_dir,
        dirs_exist_ok=True,
        symlinks=True,
        ignore=lambda _dir, names: _SINGLETON_LOCKFILES & set(names),
    )


def reset_profile(profile_dir: Path) -> None:
    """Wipe ``profile_dir`` and re-create it seeded from the real Chromium profile.

    Called before every browser launch. Reusing a run's accumulated
    profile across launches is fragile: a crashed Chromium leaves stale
    ``SingletonLock`` entries and possibly-corrupt stores, and a
    still-running previous instance holds the directory so the next
    launch hands off to it and never opens its debug port. Starting each
    browser from a freshly seeded directory makes launches deterministic.
    """
    shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    seed_profile_if_empty(profile_dir)


def _wayland_session() -> bool:
    """True when the process runs under a Wayland session.

    Wayland compositors own window placement, so Chromium silently
    ignores ``--window-position``. Callers force ``--ozone-platform=x11``
    (XWayland) alongside the flag so it is honored.
    """
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def launch_chromium(
    port: int,
    user_data_dir: str | Path,
    headless: bool = False,
    user_agent: str | None = None,
    extension_dir: str | Path | None = None,
) -> subprocess.Popen[bytes]:
    """Launch Chromium with minimal flags — only what a real user session has.

    Returns the subprocess handle with stderr captured so a startup
    failure (unusable profile, missing display, …) surfaces its real
    message instead of zendriver's generic "Failed to connect to
    browser" hint. The caller is responsible for
    ``process.terminate()`` / ``process.kill()`` on shutdown.
    """
    args = [
        _CHROMIUM_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
    ]
    if CHROMIUM_NO_SANDBOX:
        args.append("--no-sandbox")
    if headless:
        args.append("--headless=new")
    if user_agent:
        args.append(f"--user-agent={user_agent}")
    if extension_dir is not None:
        args.append(f"--load-extension={extension_dir}")
    if CHROMIUM_WINDOW_POSITION:
        if _wayland_session():
            args.append("--ozone-platform=x11")
        args.append(f"--window-position={CHROMIUM_WINDOW_POSITION}")

    logger.info("launching clean Chromium: {}", " ".join(args))
    # ``start_new_session`` puts Chromium and its forked children in one
    # process group so ``terminate_chromium`` can kill the whole tree
    # (children inherit the stderr pipe; killing only the main process
    # would leave them holding the write end).
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _drain_chromium_stderr(process: subprocess.Popen[bytes]) -> str:
    """Boundedly drain Chromium's captured stderr; never blocks indefinitely.

    Chromium's forked children inherit the stderr pipe, so a plain
    ``read()`` would block until every child dies. A select timeout caps
    the wait and returns whatever stderr arrived.
    """
    if process.stderr is None:
        return "<no stderr pipe>"
    fd = process.stderr.fileno()
    chunks: list[bytes] = []
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


def terminate_chromium(process: subprocess.Popen[bytes]) -> None:
    """TERM (then KILL) the whole Chromium process group.

    Chromium forks zygote/gpu/renderer children that inherit the stderr
    pipe; terminating only the main process leaves them holding the
    write end. ``launch_chromium`` starts Chromium in its own session,
    so killing the process group reaps the entire tree and closes the
    pipe.
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


def _chromium_pgid(entry: str, root: Path) -> tuple[int, int] | None:
    """Return ``(pid, pgid)`` if `/proc/<entry>` is a Chromium under ``root``.

    Matches on the process name containing ``chrom`` and a resolved
    ``--user-data-dir`` value that is relative to ``root``, so a stale
    instance holding a run's profile is caught but the operator's real
    browser (under ``~/.config``) never is. Own process is skipped.
    """
    try:
        pid = int(entry)
    except ValueError:
        return None
    if pid == os.getpid():
        return None
    try:
        args = Path("/proc", entry, "cmdline").read_bytes().split(b"\x00")
    except OSError:
        return None
    if not any(b"chrom" in a for a in args):
        return None
    # Arch's /usr/bin/chromium wrapper execs the real binary with the whole
    # flag set as ONE argv entry ("/usr/lib/chromium/chromium --flag ..."),
    # so match the flag anywhere in any entry, not just as an exact prefix.
    blob = next((a for a in args if b"--user-data-dir=" in a), None)
    if blob is None:
        return None
    ud = blob.split(b"--user-data-dir=", 1)[1].split(None, 1)[0].decode("utf-8", errors="replace")
    if not Path(ud).resolve().is_relative_to(root):
        return None
    try:
        return pid, os.getpgid(pid)
    except OSError:
        return None


def _pgrep_pids_under(root: Path) -> list[tuple[int, int]]:
    """Fallback for non-Linux hosts: find Chromium pids via ``pgrep``."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"user-data-dir={root}"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[tuple[int, int]] = []
    for token in out.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        try:
            pids.append((pid, os.getpgid(pid)))
        except OSError:
            continue
    return pids


def _chromium_pids_under(root: Path) -> list[tuple[int, int]]:
    """Collect ``(pid, pgid)`` for every Chromium using a profile under ``root``."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return _pgrep_pids_under(root)
    return [pgid for entry in entries if entry.isdigit() if (pgid := _chromium_pgid(entry, root)) is not None]


def _group_alive(pgid: int) -> bool:
    """Return True if the process group ``pgid`` still exists."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _term_then_kill(pgid: int) -> None:
    """TERM the group, poll ~2s for exit, then KILL it."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        if not _group_alive(pgid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_chromium_under(root_dir: Path) -> int:
    """Reap every live Chromium group whose profile dir is under ``root_dir``.

    Returns the number of process groups terminated. Safe to call with
    no matching browsers; persistent (non-temp) profile directories are
    left intact.
    """
    root = Path(root_dir).resolve()
    targets = _chromium_pids_under(root)
    for _pid, pgid in targets:
        _term_then_kill(pgid)
    if targets:
        logger.info("terminated {} Chromium group(s) under {}", len(targets), root)
    return len(targets)


async def wait_for_devtools_port(
    process: subprocess.Popen[bytes],
    port: int,
    host: str = "127.0.0.1",
    timeout_s: float = _CHROMIUM_STARTUP_TIMEOUT_S,
) -> None:
    """Wait until Chromium opens its devtools port or raise its captured stderr.

    Chromium commonly exits before binding when the profile directory is
    unusable (locked by a live instance, stale SingletonLock, corrupt
    store). Without this check ``zd.start`` blocks and eventually reports
    only the generic "Failed to connect to browser" hint. On timeout the
    process is terminated first so its stderr reaches EOF.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        if process.poll() is not None:
            raise RuntimeError(
                f"Chromium exited with code {process.returncode} before opening the debugging port.\n"
                f"stderr:\n{_drain_chromium_stderr(process)}"
            )
        try:
            _reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            pass
        else:
            writer.close()
            await writer.wait_closed()
            return
        if asyncio.get_running_loop().time() >= deadline:
            terminate_chromium(process)
            raise RuntimeError(
                f"Chromium timed out after {timeout_s:.0f}s before opening the debugging port.\n"
                f"stderr:\n{_drain_chromium_stderr(process)}"
            )
        await asyncio.sleep(_PORT_POLL_INTERVAL_S)


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
        terminate_chromium(process)

    if profile_dir is not None:
        shutil.rmtree(profile_dir, ignore_errors=True)
