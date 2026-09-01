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

import ast
import re

from loguru import logger
from pydantic_ai import RunContext

from browser_agent.agent_logging import traced_tool
from browser_agent.domain.script_execution_result import ScriptExecutionResult
from browser_agent.ports.script_runner_port import ScriptRunnerPort
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.zendriver_error_patterns import SCRIPT_TOOLS_MODULES, ZD_RUNTIME_ERROR_PATTERNS
from browser_agent.use_cases.script_precheck import precheck

VALIDATION_TIMEOUT_S = 90.0
# Discovery scripts legitimately enumerate dozens of listing pages during the
# validation run (the validation IS the full deliverable), far exceeding the
# generic processing-script budget. Match the discovery smoke-test budget.
DISCOVERY_VALIDATION_TIMEOUT_S = 600.0
_DISCOVERY_MARKERS = ("save_discovered_link", "DISCOVERY_MANIFEST")
_ERROR_HEAD_CHARS = 2000
_TIMEOUT_NOTICE_RE = re.compile(r"\[TIMEOUT[^\]]*\]")
# Consecutive pure-timeout streak (module state, like the LLM ledger). First
# pure timeout stays uncharged; each subsequent one is charged as FAILED.
_CONSECUTIVE_PURE_TIMEOUTS = 0
PROCESSING_QUEUE_VALIDATION_TIMEOUT_S = 600.0
# Ranged/queue processing scripts loop over many sessions via load_discovered_links;
# match the discovery budget so the validation loop is not wall-clock-truncated.
_PROCESSING_QUEUE_MARKERS = ("load_discovered_links(", "mark_link_processed(")

# Environmental warning-noise lines that must never become the operator-log
# tail. Python emits these around LLM-authored code (only visible when
# ResourceWarning is enabled); they are not the script's own summary.
# Substring match: the tracemalloc hint is often prefixed by
# ``RuntimeWarning:``, and ``ResourceWarning:`` appears both at line start
# and inline (``...: ResourceWarning: ...``).
_WARNING_STUB_LINES: tuple[str, ...] = (
    "Enable tracemalloc to get the object allocation traceback",
    "ResourceWarning:",
    ": ResourceWarning:",
)


def _is_warning_stub(line: str) -> bool:
    """True when ``line`` is ResourceWarning/tracemalloc noise, not script output."""
    stripped = line.strip()
    return any(stub in stripped for stub in _WARNING_STUB_LINES)


def _first_non_empty(lines: list[str]) -> str:
    """Return the first non-empty line (fallback when every line is a stub)."""
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


_SYNC_ELEMENT_PROPERTIES: frozenset[str] = frozenset({"text", "text_all", "attrs", "id"})


def _ast_static_check(python_code: str) -> str:
    """AST anti-patterns the regex table can't express reliably; "" when clean."""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and len(node.args) > 1:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "evaluate":
                return (
                    "tab.evaluate(expr, ...) with a second positional argument — zendriver "
                    "forwards no JS parameters (rule 10). Interpolate the value into the JS "
                    "string with an f-string instead; await_promise/return_by_value may be "
                    "passed as keywords only."
                )
        if isinstance(node, ast.Await):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr in _SYNC_ELEMENT_PROPERTIES:
                return (
                    "await el.<property> — el.text / el.text_all / el.attrs / el.id are SYNC "
                    "properties returning plain str/dict; awaiting one raises TypeError: "
                    "object str can't be used in 'await' expression. Use await "
                    "get_text(el, tab) (rule 0) for text or await "
                    "el.apply('(el) => el.textContent') for full subtree text."
                )
    return ""


# Statically-detectable anti-patterns in the agent's python_code. Catching
# these BEFORE running saves a wasted validation attempt on deterministic
# errors. Each entry is (regex, fix). Checked in order; first match wins.
_STATIC_CHECK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bsys\.(?:stdout|stderr)\.reconfigure\s*\("),
        "sys.stdout/sys.stderr.reconfigure(...) is NOT available inside validation runs "
        "(stdout/stderr are captured StringIO buffers). Delete the reconfigure call; print plain text.",
    ),
    (
        re.compile(
            r"^\s*(?:async\s+)?def\s+(?:get_text|get_attr|trusted_click|extract_fields|extract_rows|extract_links|goto_ready)\s*\(",
            re.MULTILINE,
        ),
        "get_text/get_attr/trusted_click/extract_fields/extract_rows/extract_links/goto_ready is "
        "redefined inline (rule 0). These are importable helpers: add "
        "the appropriate 'from script_tools.<module> import ...' at the top and "
        "DELETE your local def. The helpers MUST NOT be redefined or modified.",
    ),
    (
        re.compile(r"\bawait\s+save_record\s*\("),
        "await save_record(...) — save_record is synchronous (rule 11). Drop the await: save_record(url, {...}).",
    ),
    (
        re.compile(
            r"(?s)^(?=.*\b(?:download_pdf_browser|download_pdf_curl_cffi|download_file_browser|download_file_curl_cffi)\s*\()"
            r"(?=.*\bsave_record\s*\()"
            r"(?!.*['\"]core_html_filename['\"])"
        ),
        "save_record(...) is missing the 'core_html_filename' key (rule 13/14). "
        "When the task downloads PDFs you MUST also save the HTML of the page "
        "richest in metadata about each downloaded document — its own page, or "
        "the preceding listing/metadata-table page when that carries more of "
        "the record's fields — and link it: "
        "call result = await save_page_html(tab, out_dir, page_url) (add "
        'ready_selector="<metadata element CSS>" on SPA pages where metadata '
        "binds after load — name the late-bound metadata ITEM element (the "
        "one your extraction queries), never a heading/title like h2 or "
        ".main__container-header-title) and never a class that also matches "
        "static/server-rendered duplicates of the metadata elsewhere on the "
        "page (e.g. .document__credits-item matches the STATIC #original-text "
        "block on vLex — name an element that only exists after the binding "
        'pass, like metadata-item or ".document__credits metadata-item"), then pass '
        "\"core_html_filename\": Path(result['saved_path']).name in EVERY save_record "
        "data dict that has a core_pdf_filename. Omit the key only when no HTML was "
        "captured for that row.",
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
        re.compile(r"\bresult\s*\[\s*['\"]file_size['\"]\s*\]"),
        "result['file_size'] — the dict has no file_size key (rule 0/13). Use result['size'] for bytes.",
    ),
    (
        re.compile(r"\bimport\s+playwright\b"),
        "import playwright — Playwright is not installed (rule 8). Use zendriver's CDP API.",
    ),
    (
        re.compile(r"\bawait\s+\w+\.text_content\s*\("),
        "await el.text_content(...) — zendriver elements have no text_content() method (rule 4b). Use await get_text(el, tab) (rule 0) or await el.apply('(el) => el.textContent'). Never await el.text — .text is a SYNC property (awaiting it raises TypeError).",
    ),
    (
        re.compile(
            r"(?s)\b(\w+),\s*\w+\s*=\s*await wait_for_anchors\s*\([^)]*\)\s*"
            r"(?:[^\n]*\n){0,4}?"
            r"[^\n]*\bif\s+(?:not\s+\1\b|\1\s*==\s*0\b)"
        ),
        "wait_for_anchors(...) followed by `if count == 0:` is UNREACHABLE — "
        "wait_for_anchors RAISES TimeoutError on zero matches; it never returns 0. "
        "Wrap the gate in `try: ... except TimeoutError:` and run the modal-open "
        "fallback / metadata-gate retry (rule 14b) in the except block. Record "
        "core_download_status='load_failed' only after the retries fail.",
    ),
    (
        re.compile(
            r"\b(?:from|import)\s+script_tools\.(?!"
            + "|".join(re.escape(m) for m in SCRIPT_TOOLS_MODULES)
            + r"\b)[A-Za-z_]\w*"
        ),
        "That script_tools module does NOT exist. The ONLY available script_tools modules are: "
        + ", ".join(f"script_tools.{m}" for m in SCRIPT_TOOLS_MODULES)
        + " — no other script_tools modules exist. Note: extract_rows, extract_links, and "
        "extract_fields are all FUNCTIONS inside script_tools.extract_fields; import with: "
        "from script_tools.extract_fields import extract_fields, extract_links, extract_rows.",
    ),
]


def _static_check(python_code: str) -> str:
    """Return a failure message if ``python_code`` has a deterministic bug.

    Returns "" when no static anti-pattern matches. The checks mirror the
    runtime ZD_RUNTIME_ERROR_PATTERNS but catch the error from the source
    text alone, before spending a validation attempt.
    """
    for pattern, fix in _STATIC_CHECK_PATTERNS:
        if pattern.search(python_code):
            return fix
    ast_hit = _ast_static_check(python_code)
    if ast_hit:
        return ast_hit
    pre = precheck(python_code)
    if pre:
        return pre
    return ""


def _diagnose_zendriver_errors(output: str) -> str:
    """Return a ``# DIAGNOSIS`` block for every ZD pattern matched in ``output``.

    The block is appended to the tool return so the agent sees the same
    actionable diagnosis the operator log already had — closing the loop
    that previously discarded the diagnosis into operator-only logs.
    """
    hits = [desc for pattern, _label, desc in ZD_RUNTIME_ERROR_PATTERNS if pattern in output]
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

    Your validation script MUST finish within its timeout (~90s for a plain
    processing script). If the full strategy needs longer, BOUND the
    validation run (process a 1-2 item slice: one session, one page) — only
    the FINAL emitted GeneratedScript must be unbounded.
    """
    deps = ctx.deps
    if deps.validation_attempts >= deps.validation_limit:
        return _limit_reached(deps)
    static_fail = _static_check(python_code)
    if static_fail:
        return _static_check_failed(static_fail, deps)
    deps.validation_attempts += 1
    run_number = deps.validation_attempts
    runner: ScriptRunnerPort = deps.script_runner
    if any(m in python_code for m in _DISCOVERY_MARKERS):
        timeout = DISCOVERY_VALIDATION_TIMEOUT_S
    elif any(m in python_code for m in _PROCESSING_QUEUE_MARKERS):
        timeout = PROCESSING_QUEUE_VALIDATION_TIMEOUT_S
    else:
        timeout = VALIDATION_TIMEOUT_S
    async with traced_tool("run_validation_script"):
        result: ScriptExecutionResult = await runner.run(python_code, timeout=timeout)
    global _CONSECUTIVE_PURE_TIMEOUTS
    if _is_pure_timeout(result):
        _CONSECUTIVE_PURE_TIMEOUTS += 1
        if _CONSECUTIVE_PURE_TIMEOUTS > 1:
            # Repeated identical full-run timeout: charge it so the agent
            # cannot burn minutes re-running the same script for free.
            _log_validation_result(result, run_number, charged=True)
            return _timeout_charged(result, deps, _CONSECUTIVE_PURE_TIMEOUTS)
        # First pure timeout is environmental (slow target site), not a
        # strategy error — NOT charged. Roll the counter back before logging
        # so the logged run number matches the agent-facing bookkeeping.
        deps.validation_attempts -= 1
        _log_validation_result(result, run_number, charged=False)
        return _timeout_no_charge(result, deps.validation_attempts, deps.validation_limit)
    _CONSECUTIVE_PURE_TIMEOUTS = 0
    _log_validation_result(result, run_number, charged=True)
    if result.success and _reports_zero_variants(result.output):
        return _zero_variants_failed(deps)
    if not result.success:
        _log_zendriver_errors_in_output(result.output, run_number)
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


def _timeout_charged(result: ScriptExecutionResult, deps: AgentDeps, repeat: int) -> str:
    """Format a repeated pure timeout that IS charged as a failed attempt."""
    header = (
        f"# Validation attempt {deps.validation_attempts}/{deps.validation_limit}: FAILED (exit_code={result.exit_code})"
    )
    note = (
        f"[TIMEOUT after {VALIDATION_TIMEOUT_S:.0f}s — validation script cancelled; "
        f"repeated full-run timeout ({repeat}). Split validation into a smaller dry-run "
        "(limit rows/iterations) before running the full script.]"
    )
    return f"{header}\n\n{note}"


_ZERO_VARIANT_MARKERS: tuple[str, ...] = ("no variants to process", "0 variants", "0 records saved")


def _reports_zero_variants(output: str) -> bool:
    """True when a nominally successful run processed zero variants."""
    return any(marker in output for marker in _ZERO_VARIANT_MARKERS)


def _zero_variants_failed(deps: AgentDeps) -> str:
    """Format a false pass: success marker on stdout but nothing extracted."""
    remaining = deps.validation_limit - deps.validation_attempts
    footer = (
        f"\n# You have {remaining} validation attempt(s) remaining."
        if remaining > 0
        else "\n# This was your LAST validation attempt. Emit the final script now."
    )
    header = f"# Validation attempt {deps.validation_attempts}/{deps.validation_limit}: FAILED"
    body = (
        "Validation passed but processed 0 variants — extraction produced nothing; "
        "verify selectors and pagination before emitting."
    )
    return f"{header}\n\n{body}{footer}"


def _log_validation_result(result: ScriptExecutionResult, run_number: int, charged: bool) -> None:
    """Log every validation outcome — success or failure — with a summary.

    ``run_number`` is the sequential validation run (charged or not);
    ``charged`` records whether that run consumed a validation attempt.
    Logging after the charge decision keeps the logged run number and the
    agent-facing "attempts remaining" bookkeeping consistent.

    This fires on EVERY validation run so the operator always sees what
    the agent tested, even when the script succeeds. On failure, the root
    error line is extracted and logged. On success, the last non-empty
    line of the output (usually the agent's SUCCESS/FAIL summary print)
    is logged so the operator knows what the validation proved.
    """
    lines = result.output.strip().split("\n")
    charge_note = "" if charged else " (NOT charged — pure timeout)"
    if result.success:
        # Pick the last non-empty, non-warning-stub line so the operator
        # sees the script's real summary, not a ResourceWarning hint.
        tail_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not _is_warning_stub(stripped):
                tail_line = stripped
                break
        if not tail_line:
            tail_line = _first_non_empty(lines)
        logger.info(
            "validation run {n} PASSED — {tail}",
            n=run_number,
            tail=tail_line[:200],
        )
    else:
        # On failure, find the actual error line (skip traceback + warning noise)
        error_line = ""
        for line in reversed(lines):
            stripped = line.strip()
            if (
                stripped
                and not stripped.startswith("Traceback")
                and not stripped.startswith("File ")
                and not stripped.startswith("  ")
                and not _is_warning_stub(stripped)
            ):
                error_line = stripped
                break
        if not error_line:
            error_line = _first_non_empty(lines)
        summary = f" — {error_line}" if error_line else ""
        logger.warning(
            "validation run {n} FAILED{summary}{charge}",
            n=run_number,
            summary=summary,
            charge=charge_note,
        )


def _log_zendriver_errors_in_output(output: str, run_number: int) -> None:
    """Scan ``output`` for patterns indicating zendriver API misuse and log them."""
    found: list[str] = []
    for pattern, label, description in ZD_RUNTIME_ERROR_PATTERNS:
        if pattern in output:
            found.append(description)
            logger.warning(
                "[VALIDATION ZD-ERROR] run={n} — {label}: {description}",
                n=run_number,
                label=label,
                description=description,
            )
    if found:
        logger.warning(
            "validation run {n} — zendriver knowledge gaps: {count} issue(s) — {gaps}",
            n=run_number,
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
