"""Run the LLM's emitted validation script in-process against the agent's session.

This adapter runs the LLM's script in the **current** process. It
opens a fresh tab in the agent's already-running Chromium per
validation attempt, injects a ``start_browser()`` shim via
``sys.modules`` so the LLM's ``from script_tools.start_browser import
start_browser`` binds the validation-browser shim, inserts the run's
``scripts/`` dir at ``sys.path[0]`` so every other ``script_tools.*``
import resolves to the real copied helpers, sets env vars so
``save_record`` writes to the runner's metadata DB, neutralizes every
``asyncio.run(...)`` call via :func:`with_emitted_asyncio_run`, and
runs ``main()`` directly in the agent's event loop.

Trade-off: the agent no longer proves the emitted script is fully
self-contained by running it in a clean subprocess — but every
emitted script the operator actually runs imports the same
``script_tools/`` helpers, so the in-process check is sufficient for
selector / scroll / filter logic verification.

HAZARD: LLM-authored validation code runs in the driver's own event
loop. ``asyncio.wait_for`` cannot interrupt a synchronous block
(``while True: pass``, a blocking ``time.sleep``, a blocking C call),
so such a script hangs step 0 forever with no timeout — a hazard the
previous subprocess design did not have. A watchdog on a separate
thread, or running the LLM's ``main()`` in a worker thread with its
own loop, would bound it."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
import traceback
import types
import warnings
from pathlib import Path
from typing import Any

import zendriver as _real_zendriver
from loguru import logger

from browser_agent.adapters.emitted_asyncio_run import with_emitted_asyncio_run
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.browser_session_port import BrowserSessionPort
from browser_agent.ports.script_runner_port import ScriptRunnerPort


class InProcessScriptRunnerAdapter(ScriptRunnerPort):
    """Run validation scripts in-process against the agent's browser session.

    The runner is bound to a single :class:`BrowserSessionPort` (the
    same one the agent explores with). Each ``run`` call opens a new
    tab in that session's Chromium, executes the LLM's ``main()``
    with the tab exposed via a ``start_browser()`` shim installed in
    ``sys.modules``, and returns captured stdout/stderr as a
    :class:`ScriptExecutionResult`.

    The LLM's ``await browser.stop()`` is a no-op — closing the
    validation browser would kill the agent's session. Tabs opened
    for validation are NOT explicitly closed; the agent's session
    tears everything down on ``close()``.

    A class-level :attr:`_validation_lock` serializes ``_exec_main``
    across concurrent writer validations — two writers run in
    parallel but only one validation script executes at a time,
    preventing ``sys.modules`` / ``sys.stdout`` / ``os.environ``
    clobbering. The slow LLM thinking runs unconstrained.
    """

    _DEFAULT_TIMEOUT = 120.0
    _validation_lock = asyncio.Lock()

    def __init__(
        self,
        browser_session: BrowserSessionPort,
        metadata_db_path: Path | None = None,
        task_slug: str = "validation",
        filter_labels: list[str] | None = None,
    ) -> None:
        self._session = browser_session
        self._metadata_db_path = Path(metadata_db_path) if metadata_db_path else None
        self._task_slug = task_slug
        self._filter_labels = filter_labels

    async def run(self, python_code: str, timeout: float = _DEFAULT_TIMEOUT) -> ScriptExecutionResult:
        transformed = self._augment(python_code)
        tab = await self._session.new_tab()
        namespace = self._build_namespace(tab)
        buffer = io.StringIO()
        try:
            async with self._validation_lock:
                try:
                    return await asyncio.wait_for(
                        self._exec_main(transformed, namespace, buffer),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    partial = buffer.getvalue()
                    output = f"{partial}\n[TIMEOUT after {timeout:.0f}s — validation script cancelled]".strip()
                    return ScriptExecutionResult(exit_code=124, output=output, success=False)
        finally:
            await _close_tab_silently(tab)

    @staticmethod
    def _augment(python_code: str) -> str:
        """Neutralize ``asyncio.run(...)`` calls so they don't conflict with the running loop."""
        return with_emitted_asyncio_run(python_code)

    async def _exec_main(self, code: str, namespace: dict[str, Any], buffer: io.StringIO) -> ScriptExecutionResult:
        with _redirect_stdio_into(buffer):
            try:
                with (
                    _shim_modules(namespace["start_browser"]),
                    _env_vars_for(self._metadata_db_path, self._task_slug, self._filter_labels),
                    _sys_path_insert(self._metadata_db_path),
                    _restore_warning_filters(),
                ):
                    compiled = compile(code, "<validation_script>", "exec")
                    exec(compiled, namespace)
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

        The shim is installed into ``sys.modules["script_tools.start_browser"]``
        in ``_exec_main`` so the LLM's ``from script_tools.start_browser
        import start_browser`` binds it. Every other ``script_tools.*``
        import resolves to the real copied helpers via ``sys.path[0]``.

        ``__file__`` points inside the runner's ``scripts/`` directory.
        The DB path and task slug are set via env vars (``_env_vars``)
        rather than namespace globals — unlike the exec namespace's
        ``__file__``, ``save_record`` reads the real ``__main__``
        module's ``__file__``, so only the env var is load-bearing.
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
            raise AttributeError(f"browser has no underlying zendriver instance; cannot forward .{name}")
        return getattr(self._real_browser, name)


def _build_start_browser(wrapper: _ValidationBrowser) -> Any:
    """Return an async ``start_browser()`` shim that yields ``wrapper``.

    The shim's signature matches the ``script_tools.start_browser``
    helper so LLM-emitted code is happy — but the arguments are
    ignored; the agent owns the real launch.
    """

    async def start_browser(
        headless: Any = None,
        user_data_dir: Any = None,
    ) -> _ValidationBrowser:
        return wrapper

    return start_browser


@contextlib.contextmanager
def _shim_modules(start_browser: Any):
    """Install shims in ``sys.modules`` for the validation duration.

    Replaces ``zendriver`` with a wrapper whose ``start`` is the
    ``start_browser`` shim (so ``await zd.start(...)`` works), and
    installs ``script_tools.start_browser`` as a module exposing the
    same shim (so ``from script_tools.start_browser import start_browser``
    binds it). Every other ``script_tools.*`` import resolves to the
    real copied helpers via ``sys.path[0]``. On exit the real modules
    are restored.
    """
    zd_wrapper = types.ModuleType("zendriver")
    zd_wrapper.__dict__.update(_real_zendriver.__dict__)
    zd_wrapper.start = start_browser

    st_wrapper = types.ModuleType("script_tools.start_browser")
    st_wrapper.start_browser = start_browser

    previous_zd = sys.modules.get("zendriver")
    previous_st = sys.modules.get("script_tools.start_browser")
    sys.modules["zendriver"] = zd_wrapper
    sys.modules["script_tools.start_browser"] = st_wrapper
    try:
        yield
    finally:
        if previous_zd is None:
            sys.modules.pop("zendriver", None)
        else:
            sys.modules["zendriver"] = previous_zd
        if previous_st is None:
            sys.modules.pop("script_tools.start_browser", None)
        else:
            sys.modules["script_tools.start_browser"] = previous_st


@contextlib.contextmanager
def _env_vars_for(
    metadata_db_path: Path | None,
    task_slug: str,
    filter_labels: list[str] | None = None,
):
    """Set save_record env vars around the exec, restoring afterward."""
    if metadata_db_path is None:
        yield
        return
    saved_db = os.environ.get("BROWSER_AGENT_SAVE_RECORD_DB_PATH")
    saved_slug = os.environ.get("BROWSER_AGENT_TASK_SLUG")
    saved_labels = os.environ.get("BROWSER_AGENT_SUBTASK_FILTER_LABELS")
    os.environ["BROWSER_AGENT_SAVE_RECORD_DB_PATH"] = str(metadata_db_path)
    os.environ["BROWSER_AGENT_TASK_SLUG"] = task_slug
    if filter_labels:
        os.environ["BROWSER_AGENT_SUBTASK_FILTER_LABELS"] = json.dumps(filter_labels)
    try:
        yield
    finally:
        if saved_db is None:
            os.environ.pop("BROWSER_AGENT_SAVE_RECORD_DB_PATH", None)
        else:
            os.environ["BROWSER_AGENT_SAVE_RECORD_DB_PATH"] = saved_db
        if saved_slug is None:
            os.environ.pop("BROWSER_AGENT_TASK_SLUG", None)
        else:
            os.environ["BROWSER_AGENT_TASK_SLUG"] = saved_slug
        if saved_labels is None:
            os.environ.pop("BROWSER_AGENT_SUBTASK_FILTER_LABELS", None)
        else:
            os.environ["BROWSER_AGENT_SUBTASK_FILTER_LABELS"] = saved_labels


@contextlib.contextmanager
def _sys_path_insert(metadata_db_path: Path | None):
    """Insert the run's ``scripts/`` dir at ``sys.path[0]`` for the exec duration.

    The source ``script_tools/`` package (``src/browser_agent/script_tools``)
    is also inserted as a lower-priority fallback so ``script_tools.*``
    imports resolve even when the emit-time copy beside the run is absent
    (e.g. validation runs before ``ScriptToolsCopier`` has emitted). Runs'
    copied helpers are inserted on top and take precedence; both entries are
    removed in ``finally``.
    """
    source_dir = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(source_dir))
    if metadata_db_path is not None:
        scripts_dir = metadata_db_path.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
    try:
        yield
    finally:
        for entry in (str(source_dir), str(scripts_dir) if metadata_db_path is not None else None):
            if entry is None:
                continue
            try:
                sys.path.remove(entry)
            except ValueError:
                pass


@contextlib.contextmanager
def _restore_warning_filters():
    """Snapshot ``warnings.filters`` on entry and restore it in ``finally``.

    LLM-authored validation code may call ``warnings.simplefilter`` /
    ``warnings.filterwarnings``; those mutate the process-global
    ``warnings.filters`` list in place. Without restoration the mutation
    leaks into later validation runs, repair loops, and the driver
    process (e.g. a ``simplefilter("always")`` would make every
    subsequent run emit ResourceWarning noise and slow down execution).
    The list is restored by slice assignment so any reference held by
    ``warnings`` internals stays valid.
    """
    saved_filters = list(warnings.filters)
    try:
        yield
    finally:
        warnings.filters[:] = saved_filters


@contextlib.contextmanager
def _redirect_stdio_into(buffer: io.StringIO):
    """Swap stdout/stderr to capture the validation script's prints into ``buffer``.

    The buffer is owned by the caller (``run``) so partial output
    survives ``asyncio.wait_for`` cancellation — on timeout the caller
    can still read whatever the script printed before it was killed.
    NOTE: this swaps the process-global ``sys.stdout`` / ``sys.stderr``
    around LLM-authored code. Safe today because loguru binds its sink
    object at configure time, but a future lazily-resolving sink would
    start leaking framework logs into the agent's validation output.
    """
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = buffer
    sys.stderr = buffer
    try:
        yield
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
