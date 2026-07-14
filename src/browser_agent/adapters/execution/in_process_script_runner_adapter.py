"""Run the LLM's emitted validation script in-process against the agent's session.

The previous design spawned a subprocess for every validation attempt and
the subprocess launched its own Chromium via the ``emitted_clean_launch``
helper. With ``MAX_VALIDATION_ATTEMPTS = 3`` that meant up to four
Chromium windows per agent run (one persistent session + three
validation subprocesses) and the user's anti-bot defence saw a fresh
fingerprint per window.

This adapter runs the LLM's script in the **current** process. It
opens a fresh tab in the agent's already-running Chromium per
validation attempt, injects a ``start_browser()`` shim that returns
that tab inside the agent's browser, installs a wrapped ``zendriver``
module in ``sys.modules`` so the LLM's ``import zendriver as zd`` also
picks up the shim, prepends the page-wait and save-record helpers
(which work on any tab), strips the ``asyncio.run(main())`` wrapper
the LLM emits, replaces the vendored ``save_record`` with one that
writes to the runner's metadata DB path, and runs ``main()`` directly
in the agent's event loop.

Trade-off: the agent no longer proves the emitted script is fully
self-contained by running it in a clean subprocess — but every
emitted script the operator actually runs is still written through
the same vendored helpers, so the in-process check is sufficient for
selector / scroll / filter logic verification.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import json
import re
import sqlite3
import sys
import traceback
import types
from pathlib import Path
from typing import Any

import zendriver as _real_zendriver
from loguru import logger

from browser_agent.adapters.emitted_page_wait import with_emitted_page_wait
from browser_agent.adapters.emitted_save_record import with_emitted_save_record
from browser_agent.adapters.emitted_pdf_download import with_emitted_all_pdf_downloads
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.ports.script_runner_port import ScriptRunnerPort

_ASYNCIO_ENTRYPOINT_RE = re.compile(
    r"^\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:\s*\n\s*asyncio\.run\(\s*main\s*\(\s*\)\s*\)\s*$",
    re.MULTILINE,
)


class InProcessScriptRunnerAdapter(ScriptRunnerPort):
    """Run validation scripts in-process against the agent's browser session.

    The runner is bound to a single :class:`BrowserSessionPort` (the
    same one the agent explores with). Each ``run`` call opens a new
    tab in that session's Chromium, executes the LLM's ``main()``
    with the tab exposed via a vendored-style ``start_browser()``
    shim, and returns captured stdout/stderr as a
    :class:`ScriptExecutionResult`.

    The LLM's ``await browser.stop()`` is a no-op — closing the
    validation browser would kill the agent's session. Tabs opened
    for validation are NOT explicitly closed; the agent's session
    tears everything down on ``close()``.
    """

    _DEFAULT_TIMEOUT = 120.0

    def __init__(
        self,
        browser_session: BrowserSessionPort,
        metadata_db_path: Path | None = None,
        task_slug: str = "validation",
    ) -> None:
        self._session = browser_session
        self._metadata_db_path = Path(metadata_db_path) if metadata_db_path else None
        self._task_slug = task_slug

    async def run(self, python_code: str, timeout: float = _DEFAULT_TIMEOUT) -> ScriptExecutionResult:
        augmented = with_emitted_page_wait(python_code)
        augmented = with_emitted_save_record(augmented)
        augmented = with_emitted_all_pdf_downloads(augmented)
        tab = await self._session.new_tab()
        namespace = self._build_namespace(tab)
        transformed = self._strip_asyncio_entrypoint(augmented)
        try:
            try:
                return await asyncio.wait_for(
                    self._exec_main(transformed, namespace),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return ScriptExecutionResult(
                    exit_code=124,
                    output=f"[TIMEOUT after {timeout:.0f}s — validation script cancelled]",
                    success=False,
                )
        finally:
            await _close_tab_silently(tab)

    @staticmethod
    def _strip_asyncio_entrypoint(code: str) -> str:
        """Remove the ``if __name__ == "__main__": asyncio.run(main())`` trailer.

        We are already in an event loop, so calling ``asyncio.run``
        would raise. The runner instead awaits ``main()`` directly
        (see :meth:`_exec_main`). The trailer removal is a no-op if
        the LLM did not emit it.
        """
        return _ASYNCIO_ENTRYPOINT_RE.sub("", code)

    async def _exec_main(self, code: str, namespace: dict[str, Any]) -> ScriptExecutionResult:
        with _redirect_stdio() as buffer:
            try:
                with _shim_zendriver_in_sys_modules(namespace["start_browser"]):
                    compiled = compile(code, "<validation_script>", "exec")
                    exec(compiled, namespace)
                    if self._metadata_db_path is not None:
                        # Replace the vendored save_record with one
                        # that writes to the runner's metadata DB
                        # path. The replacement runs after exec so
                        # the vendored block's definition is
                        # overridden before ``main()`` is awaited.
                        namespace["save_record"] = _build_save_record(self._metadata_db_path, self._task_slug)
                    main = namespace.get("main")
                    if main is None:
                        return ScriptExecutionResult(
                            exit_code=1,
                            output=f"{buffer.getvalue()}\n[no main() defined in validation script]".strip(),
                            success=False,
                        )
                    await main()
                return ScriptExecutionResult(exit_code=0, output=buffer.getvalue(), success=True)
            except SystemExit as exc:
                code_exit = int(exc.code) if exc.code is not None else 0
                return ScriptExecutionResult(
                    exit_code=code_exit,
                    output=buffer.getvalue(),
                    success=code_exit == 0,
                )
            except Exception:
                tb = traceback.format_exc()
                return ScriptExecutionResult(
                    exit_code=1,
                    output=f"{buffer.getvalue()}\n{tb}".strip(),
                    success=False,
                )

    def _build_namespace(self, tab: Any) -> dict[str, Any]:
        """Build the globals the LLM's code runs in.

        Injects the agent's tab via a ``start_browser()`` shim that
        returns a wrapper around the agent's ``zd.Browser``. The
        wrapper's ``main_tab`` is the freshly opened validation tab;
        ``stop()`` is a no-op so closing the validation browser does
        not kill the agent's session. All other browser attributes
        are passed through to the real browser so the LLM's
        ``browser.get(url, new_tab=True)`` etc. still work.

        When a metadata DB path is configured, this namespace also
        carries ``_SAVE_RECORD_DB_PATH`` and ``_SAVE_RECORD_TASK_SLUG``
        so the vendored save-record helper writes to the runner's
        ``metadata.db`` instead of deriving a path from ``__file__``.
        ``__file__`` itself points inside the runner's ``scripts/``
        directory so any fall-back path resolution stays inside the
        runner folder.
        """
        real_browser = _unwrap_browser(self._session)
        wrapper = _ValidationBrowser(real_browser, tab)
        ns: dict[str, Any] = {
            "__name__": "__validation__",
            "asyncio": asyncio,
        }
        if self._metadata_db_path is not None:
            run_path = self._metadata_db_path.parent
            scripts_dir = run_path / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            ns["__file__"] = str(scripts_dir / "validation.py")
            ns["_SAVE_RECORD_DB_PATH"] = str(self._metadata_db_path)
            ns["_SAVE_RECORD_TASK_SLUG"] = self._task_slug
        else:
            ns["__file__"] = "<validation>"
        ns["start_browser"] = _build_start_browser(wrapper)
        return ns


def _unwrap_browser(session: BrowserSessionPort) -> Any:
    """Return the underlying ``zd.Browser`` from the session.

    The default adapter stores it as ``_browser``; other adapters
    may differ. We probe a few common names and fall back to
    ``None`` if the session does not expose one — the validation
    can still run on a session that has no raw browser, but the
    wrapper will not be able to pass through additional
    ``browser.<method>`` calls.
    """
    for attr in ("_browser", "browser", "_zd_browser"):
        candidate = getattr(session, attr, None)
        if candidate is not None:
            return candidate
    return None


class _ValidationBrowser:
    """A minimal wrapper that hands the LLM a fresh tab in the agent's browser.

    Exposes ``main_tab`` (the per-validation tab) and ``stop()``
    (no-op). Other attributes fall through to the real
    ``zd.Browser`` so the LLM can call ``browser.get(url,
    new_tab=True)`` etc. without surprises.
    """

    def __init__(self, real_browser: Any, tab: Any) -> None:
        self._real_browser = real_browser
        self.main_tab = tab

    async def stop(self) -> None:
        """No-op: the agent's session owns the browser lifetime."""
        return None

    def __getattr__(self, name: str) -> Any:
        if self._real_browser is None:
            raise AttributeError(f"browser has no underlying zendriver instance; " f"cannot forward .{name}")
        return getattr(self._real_browser, name)


def _build_start_browser(wrapper: _ValidationBrowser) -> Any:
    """Return an async ``start_browser()`` shim that yields ``wrapper``.

    The shim's signature matches the vendored helper
    (``headless=False, user_data_dir=None, user_agent=None``) so
    LLM-emitted code is happy — but the arguments are ignored; the
    agent owns the real launch.
    """

    async def start_browser(
        headless: bool = False,
        user_data_dir: Any = None,
        user_agent: Any = None,
    ) -> _ValidationBrowser:
        return wrapper

    return start_browser


def _build_save_record(db_path: Path, task_slug: str) -> Any:
    """Return a ``save_record`` closure bound to ``db_path`` and ``task_slug``.

    The closure writes to the same SQLite file the final emitted
    script uses, so validation's records and the operator-run
    scraper's records live side by side in ``metadata.db``.
    """

    def save_record(source_url: str, data: dict) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(source_url TEXT PRIMARY KEY, task_slug TEXT NOT NULL, "
                "scraped_at TEXT NOT NULL, data TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata " "(source_url, task_slug, scraped_at, data) VALUES (?, ?, ?, ?)",
                (
                    source_url,
                    task_slug,
                    datetime.datetime.now(datetime.UTC).isoformat(),
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    return save_record


@contextlib.contextmanager
def _shim_zendriver_in_sys_modules(start_browser: Any):
    """Install a wrapped zendriver in ``sys.modules`` for the duration.

    The LLM's emitted code does ``import zendriver as zd`` and then
    ``await zd.start(...)``. We replace the real module with one
    whose ``start`` is the ``start_browser`` shim (and whose other
    attributes are passed through). On exit the real module is
    restored so the rest of the process is unaffected.
    """
    wrapper = types.ModuleType("zendriver")
    wrapper.__dict__.update(_real_zendriver.__dict__)
    wrapper.start = start_browser
    previous = sys.modules.get("zendriver")
    sys.modules["zendriver"] = wrapper
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("zendriver", None)
        else:
            sys.modules["zendriver"] = previous


@contextlib.contextmanager
def _redirect_stdio():
    """Swap stdout/stderr to capture the validation script's prints."""
    real_out, real_err = sys.stdout, sys.stderr
    buffer = io.StringIO()
    sys.stdout = buffer
    sys.stderr = buffer
    try:
        yield buffer
    finally:
        sys.stdout = real_out
        sys.stderr = real_err


async def _close_tab_silently(tab: Any) -> None:
    """Close ``tab`` if the zendriver API exposes it; swallow errors.

    The validation runner opens a fresh tab per attempt. Closing
    it on exit keeps the agent's browser window tidy (no
    accumulation of dead tabs) and avoids relying on the
    session's ``close()`` to clean everything up.
    """
    try:
        closer = getattr(tab, "close", None)
        if closer is not None:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
    except Exception:
        logger.exception("failed to close validation tab")
