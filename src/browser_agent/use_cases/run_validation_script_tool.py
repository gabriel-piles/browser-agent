"""The ``run_validation_script`` tool bound to the Pydantic-AI agent.

The tool takes a self-contained Python script (the same shape as the
final deliverable), runs it in a subprocess via the injected
:class:`ScriptRunnerPort`, and returns the exit code + combined
stdout/stderr. The agent uses this to validate its selectors, scroll
loops and filter logic *before* producing the final script.

A hard counter on :class:`AgentDeps` caps how many validation runs
one agent turn may perform (``MAX_VALIDATION_ATTEMPTS``). The system
prompt asks for "max 3" but LLMs routinely ignore prose limits and
loop until the request budget is exhausted; this counter is the
backstop that forces the agent to emit a final script instead of
retrying forever.
"""

from __future__ import annotations

import re

from loguru import logger
from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.script_runner_port import ScriptRunnerPort
from browser_agent.use_cases.agent_deps import AgentDeps

VALIDATION_TIMEOUT_S = 90.0
_ERROR_HEAD_CHARS = 2000
_TIMEOUT_NOTICE_RE = re.compile(r"\[TIMEOUT[^\]]*\]")

# Zendriver runtime error patterns that indicate the agent doesn't understand
# zendriver's API surface. Each description carries the fix so the diagnosis
# returned to the agent is self-contained. Mirrors step_0_generate_script.py.
_ZD_RUNTIME_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        "tab.evaluate",
        "evaluate() missing 1 required positional argument",
        'tab.evaluate called without an expression argument. Fix: pass a JS expression string as the first argument, e.g. await tab.evaluate("document.title").',
    ),
    (
        "TypeError: object NoneType can't be used in 'await' expression",
        "save_record sync",
        "save_record is synchronous (rule 11) — awaiting None raises TypeError. Fix: call it bare: save_record(url, {...}), never await save_record(...).",
    ),
    (
        "AttributeError: module 'zendriver' has no attribute 'start'",
        "zd.start not found",
        "zendriver has no top-level start(). Fix: use the vendored start_browser() helper (rule 0) — NEVER zd.start().",
    ),
    (
        "TypeError: 'NoneType' object is not callable",
        "NoneType called",
        "A zendriver object was None — wrong browser startup. Fix: use start_browser() (rule 0) and check the tab is non-None before calling methods on it.",
    ),
    (
        "TimeoutError: wait_for_anchors timed out after",
        "wait_for_anchors timeout",
        "wait_for_anchors found zero matches (rule 0). Fix: verify the CSS selector with explore_page extract first; the selector is wrong or the element never loads.",
    ),
    (
        "ModuleNotFoundError: No module named 'playwright'",
        "playwright import",
        "Playwright is not installed and must not be used. Fix: use zendriver's CDP API (tab.query_selector_all, tab.evaluate) — never import playwright.",
    ),
    (
        "KeyError: 'file_size'",
        "file_size key",
        "The result dict has no 'file_size' key (rule 0/13). Fix: read result['size'] for bytes and result['saved_path'] for the filename.",
    ),
    (
        "zendriver.core.connection.ProtocolException",
        "ProtocolException",
        "Bad tab.evaluate() call (rule 10). Fix: use a bare expression or an IIFE (() => { ... })(); never pass a function declaration; never pass a second positional argument to tab.evaluate.",
    ),
    (
        "zendriver.core.elements.ElementNotFound",
        "ElementNotFound",
        "Element not found — wrong selector or page not ready. Fix: await wait_for_anchors(tab, selector) before reading; verify the selector with explore_page.",
    ),
    (
        "NameError: name '",
        "NameError",
        "Undefined variable — wrong API name. Fix: check spelling against the vendored helper signatures in rule 0.",
    ),
    (
        "SyntaxError: invalid syntax",
        "SyntaxError",
        "Malformed Python. Fix: check for unbalanced parens/quotes, especially in f-strings and tab.evaluate JS strings.",
    ),
    (
        "ImportError",
        "Wrong import name. Fix: import only from script_tools.<module> (rule 0) — modules: save_record (save_record, load_failed_downloads), save_page_html (save_page_html), pdf_download (download_pdf_curl_cffi, download_pdf_browser), page_wait (wait_for_page_ready, wait_for_anchors, prepare_page_wait), start_browser (start_browser).",
    ),
    (
        "ModuleNotFoundError: No module named '",
        "ModuleNotFoundError",
        "Missing module. Fix: only zendriver, asyncio, stdlib, and script_tools.* are available (rule 0/5); the script_tools/ folder is copied beside the script at emit time.",
    ),
    (
        "AttributeError: '",
        "AttributeError",
        "Wrong method/property on a zendriver element. Fix: use el.attrs.get('href'), el.text (first text node only — rule 4b), or await el.apply('(el) => el.textContent') for full text.",
    ),
    (
        "TypeError: ",
        "TypeError",
        "Wrong argument type. Fix: check rule 4b — never pass a second positional to tab.evaluate; interpolate values into the JS string with f-strings instead.",
    ),
    (
        "asyncio.run() cannot be called from a running event loop",
        "asyncio.run error",
        "asyncio.run() inside a running loop. Fix: the script's top-level is asyncio.run(main()) — never call asyncio.run() from inside an async function.",
    ),
]

# Statically-detectable anti-patterns in the agent's python_code. Catching
# these BEFORE running saves a wasted validation attempt on deterministic
# errors. Each entry is (regex, fix). Checked in order; first match wins.
_STATIC_CHECK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bawait\s+save_record\s*\("),
        "await save_record(...) — save_record is synchronous (rule 11). Drop the await: save_record(url, {...}).",
    ),
    (
        re.compile(r"\bzd\.start\s*\("),
        "zd.start(...) — zendriver has no top-level start() (rule 0). Use start_browser() instead.",
    ),
    (
        re.compile(r"\btab\.evaluate\s*\(\s*\(?\s*=>"),
        'tab.evaluate(() => ...) without trailing () — a bare arrow function is never invoked (rule 10). Wrap as an IIFE: tab.evaluate("(() => { ... })()").',
    ),
    (
        re.compile(r"\btab\.evaluate\s*\([^,)]+,[^)]"),
        "tab.evaluate(expr, ...) with a second positional argument — zendriver forwards no args (rule 4b/10). Interpolate the value into the JS string with an f-string instead.",
    ),
    (
        re.compile(r"\bresult\s*\[\s*['\"]file_size['\"]\s*\]"),
        "result['file_size'] — the dict has no file_size key (rule 0/13). Use result['size'] for bytes.",
    ),
    (
        re.compile(r"\bimport\s+playwright\b"),
        "import playwright — Playwright is not installed (rule 8). Use zendriver's CDP API.",
    ),
    (
        re.compile(r"\bawait\s+\w+\.text_content\s*\("),
        "await el.text_content(...) — zendriver elements have no text_content() method (rule 4b). Use await el.apply('(el) => el.textContent') or el.text.",
    ),
]


def _static_check(python_code: str) -> str:
    """Return a failure message if ``python_code`` has a deterministic bug.

    Returns "" when no static anti-pattern matches. The checks mirror the
    runtime _ZD_RUNTIME_ERROR_PATTERNS but catch the error from the source
    text alone, before spending a validation attempt.
    """
    for pattern, fix in _STATIC_CHECK_PATTERNS:
        if pattern.search(python_code):
            return fix
    return ""


def _diagnose_zendriver_errors(output: str) -> str:
    """Return a ``# DIAGNOSIS`` block for every ZD pattern matched in ``output``.

    The block is appended to the tool return so the agent sees the same
    actionable diagnosis the operator log already had — closing the loop
    that previously discarded the diagnosis into operator-only logs.
    """
    hits = [desc for pattern, _label, desc in _ZD_RUNTIME_ERROR_PATTERNS if pattern in output]
    if not hits:
        return ""
    lines = ["# DIAGNOSIS — zendriver knowledge gap(s) detected in this run:"]
    for desc in hits:
        lines.append(f"  - {desc}")
    return "\n".join(lines)


async def run_validation_script(ctx: RunContext[AgentDeps], python_code: str) -> str:
    """Run ``python_code`` in a subprocess and return the result.

    Use this tool to TEST a single script that proves your FULL
    strategy — navigate to the target URL, find the key selectors,
    click ONE filter, scroll ONCE, and print what it discovers
    (element counts, text, hrefs) — all in the same script. Pack
    every check you need into ONE script so you don't waste attempts.
    If the validation script fails, read the error output, fix your
    approach, and re-run. Only emit the final :class:`GeneratedScript`
    once a validation script succeeds.

    The script must be self-contained (imports its own dependencies,
    uses zendriver, ``asyncio.run(main())``) — exactly like the final
    deliverable.

    You have a HARD limit of ``validation_limit`` attempts per turn.
    When the limit is reached the tool refuses to run and tells you
    to emit the best script you can from the exploration you already
    did — do NOT keep retrying.
    """
    deps = ctx.deps
    if deps.validation_attempts >= deps.validation_limit:
        return _limit_reached(deps)
    static_fail = _static_check(python_code)
    if static_fail:
        return _static_check_failed(static_fail, deps)
    deps.validation_attempts += 1
    runner: ScriptRunnerPort = deps.script_runner
    async with traced_tool("run_validation_script"):
        result: ScriptExecutionResult = await runner.run(python_code, timeout=VALIDATION_TIMEOUT_S)
    _log_validation_result(result, deps.validation_attempts)
    if not result.success:
        _log_zendriver_errors_in_output(result.output, deps.validation_attempts)
    if _is_pure_timeout(result):
        deps.validation_attempts -= 1
        return _timeout_no_charge(result, deps.validation_attempts, deps.validation_limit)
    return _format_result(result, deps.validation_attempts, deps.validation_limit)


def _is_pure_timeout(result: ScriptExecutionResult) -> bool:
    """True when a timeout produced no partial diagnostics before the notice."""
    if result.exit_code != 124:
        return False
    stripped = _TIMEOUT_NOTICE_RE.sub("", result.output)
    return not stripped.strip()


def _limit_reached(deps: AgentDeps) -> str:
    return (
        f"# Validation limit reached ({deps.validation_limit}/{deps.validation_limit}).\n"
        "You have used all your validation attempts. STOP calling this tool.\n"
        "Emit the final GeneratedScript now using the selectors and patterns\n"
        "you verified during exploration. Do not call run_validation_script again."
    )


def _static_check_failed(fix: str, deps: AgentDeps) -> str:
    """Format a static-check failure that did NOT consume an attempt."""
    remaining = deps.validation_limit - deps.validation_attempts
    return (
        "# STATIC CHECK FAILED — your script has a deterministic bug.\n"
        "This did NOT consume a validation attempt. Fix the issue and re-run.\n\n"
        f"# Problem: {fix}\n\n"
        f"# You have {remaining} validation attempt(s) remaining."
    )


def _format_result(result: ScriptExecutionResult, attempt: int, limit: int) -> str:
    status = "SUCCESS" if result.success else f"FAILED (exit_code={result.exit_code})"
    header = f"# Validation attempt {attempt}/{limit}: {status}"
    body = result.output if result.success else _extract_error(result.output)
    remaining = limit - attempt
    if result.success:
        # On success, command the agent to stop and emit.
        # A neutral "N attempts remaining" encourages wasteful re-validation
        # of the final script when the strategy is already proven.
        footer = "\n# VALIDATION PASSED — proceed to emit the final GeneratedScript. Do NOT call this tool again."
    else:
        footer = (
            f"\n# You have {remaining} validation attempt(s) remaining."
            if remaining > 0
            else "\n# This was your LAST validation attempt. Emit the final script now."
        )
    diagnosis = "" if result.success else _diagnose_zendriver_errors(result.output)
    if diagnosis:
        return f"{header}\n\n{body}\n\n{diagnosis}{footer}"
    return f"{header}\n\n{body}{footer}"


def _timeout_no_charge(result: ScriptExecutionResult, attempt: int, limit: int) -> str:
    """Format a timeout that did NOT consume an attempt."""
    remaining = limit - attempt
    header = f"# Validation TIMEOUT (not charged; {remaining} attempt(s) still remaining)"
    note = (
        "The script produced no output before the timeout — likely a slow\n"
        "target site, not a strategy error. Re-run the same script; the\n"
        f"timeout was {VALIDATION_TIMEOUT_S:.0f}s. If it times out again,\n"
        "simplify the script (fewer navigations, skip PDF downloads)."
    )
    return f"{header}\n\n{result.output}\n\n{note}"


def _log_validation_result(result: ScriptExecutionResult, attempt: int) -> None:
    """Log every validation outcome — success or failure — with a summary.

    This fires on EVERY validation run so the operator always sees what
    the agent tested, even when the script succeeds. On failure, the root
    error line is extracted and logged. On success, the last non-empty
    line of the output (usually the agent's SUCCESS/FAIL summary print)
    is logged so the operator knows what the validation proved.
    """
    lines = result.output.strip().split("\n")
    tail_line = ""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            tail_line = stripped
            break
    if result.success:
        logger.info(
            "validation attempt {a} PASSED — {tail}",
            a=attempt,
            tail=tail_line[:200],
        )
    else:
        # On failure, find the actual error line (skip traceback noise)
        error_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("Traceback")
                and not stripped.startswith("File ")
                and not stripped.startswith("  ")
            ):
                error_line = stripped
                break
        summary = f" — {error_line}" if error_line else ""
        logger.warning(
            "validation attempt {a} FAILED{summary}",
            a=attempt,
            summary=summary,
        )


def _log_zendriver_errors_in_output(output: str, attempt: int) -> None:
    """Scan ``output`` for patterns indicating zendriver API misuse and log them."""
    found: list[str] = []
    for pattern, label, description in _ZD_RUNTIME_ERROR_PATTERNS:
        if pattern in output:
            found.append(description)
            logger.warning(
                "[VALIDATION ZD-ERROR] attempt={a} — {label}: {description}",
                a=attempt,
                label=label,
                description=description,
            )
    if found:
        logger.warning(
            "validation attempt {a} — zendriver knowledge gaps: {count} issue(s) — {gaps}",
            a=attempt,
            count=len(found),
            gaps="; ".join(found),
        )


def _extract_error(output: str) -> str:
    """Keep the printed diagnostics (head) AND the last traceback.

    Step 7 is built around the prints — counts, sample hrefs,
    label-vs-badge comparisons. A script that proved 8 of 9 checks
    and crashed on the 9th must not lose the evidence of what
    already worked. We keep the head (everything before the last
    traceback) capped to ``_ERROR_HEAD_CHARS``, then the last
    ``Traceback`` block (inclusive), with a marker between them.
    """
    marker = "Traceback (most recent call last)"
    idx = output.rfind(marker)
    if idx == -1:
        return output[-3000:] if len(output) > 3000 else output
    head = output[:idx].rstrip()
    tail = output[idx:]
    if len(head) > _ERROR_HEAD_CHARS:
        head = head[:_ERROR_HEAD_CHARS] + "\n…(truncated head)"
    if not head:
        return tail[-3000:]
    return f"{head}\n\n--- traceback ---\n{tail}"
