from __future__ import annotations

import ast
import re
from pathlib import Path

from browser_agent.domain.lint_finding import LintFinding

_HTTP_MODULES = frozenset({"requests", "httpx", "aiohttp", "urllib", "urllib3"})
_ALLOWED_URL_MODULES = frozenset({"urllib.parse"})
_HTTP_MSG = "no HTTP libraries; use tab.get() and script_tools download helpers"
_SELF_MSG = (
    "script imports must be stdlib, zendriver, or script_tools.* (real modules copied beside the script at emit time)"
)
_SCRIPT_TOOLS_PKG_MSG = (
    "never 'from script_tools import X' — script_tools is a package of modules, "
    "not an __init__ that re-exports names. Use 'from script_tools.start_browser import start_browser', "
    "'from script_tools.save_record import save_record', etc."
)
_EVAL_IIFE_TAIL = re.compile(r"\)\s*\(\s*\)")


def _line_of(python_code: str, pos: int) -> int:
    return python_code.count("\n", 0, pos) + 1


def _check_syntax(python_code: str) -> list[LintFinding]:
    try:
        compile(python_code, "<emitted>", "exec")
    except SyntaxError as exc:
        return [LintFinding(rule="syntax", severity="error", message=str(exc.msg), line=exc.lineno)]
    return []


def _check_save_record(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for match in re.finditer(r"\bawait\s+save_record\s*\(", python_code):
        out.append(
            LintFinding(
                rule="11",
                severity="error",
                message="save_record is synchronous; never await it",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


_DISCOVERY_NO_SAVE_MSG = (
    "discovery script MUST call save_discovered_link(url, label) for every "
    "discovered link — printing to stdout is not enough; the processing "
    "script reads links from load_discovered_links() which reads the DB"
)


def _check_discovery_save_link(python_code: str) -> list[LintFinding]:
    """Flag discovery scripts that never call ``save_discovered_link``."""
    if "save_discovered_link" not in python_code:
        return [LintFinding(rule="2b", severity="error", message=_DISCOVERY_NO_SAVE_MSG, line=1)]
    return []


def _call_args(python_code: str, open_paren: int) -> str:
    """Text from the '(' at open_paren through its matching ')' (inclusive)."""
    depth = 1
    i = open_paren + 1
    while i < len(python_code) and depth > 0:
        if python_code[i] == "(":
            depth += 1
        elif python_code[i] == ")":
            depth -= 1
        i += 1
    return python_code[open_paren:i]


def _check_download_status(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for match in re.finditer(r"\bsave_record\s*\(", python_code):
        call_text = _call_args(python_code, match.end() - 1)
        has_pdf = '"pdf_filename"' in call_text or "'pdf_filename'" in call_text
        has_dl = '"download_status"' in call_text or "'download_status'" in call_text
        if has_pdf and not has_dl:
            out.append(
                LintFinding(
                    rule="14",
                    severity="error",
                    message="save_record with pdf_filename must include download_status",
                    line=_line_of(python_code, match.start()),
                )
            )
    return out


def _check_supporting_status(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for match in re.finditer(r"\bsave_record\s*\(", python_code):
        call_text = _call_args(python_code, match.end() - 1)
        has_supporting = '"supporting_filename"' in call_text or "'supporting_filename'" in call_text
        if not has_supporting:
            continue
        has_dl = '"download_status"' in call_text or "'download_status'" in call_text
        has_role = '"download_role"' in call_text or "'download_role'" in call_text
        has_pdf = '"pdf_filename"' in call_text or "'pdf_filename'" in call_text
        if not has_dl or not has_role or has_pdf:
            out.append(
                LintFinding(
                    rule="14",
                    severity="error",
                    message=(
                        "save_record with supporting_filename must set download_status + "
                        'download_role="supporting" and must NOT set pdf_filename '
                        "(roles are mutually exclusive)"
                    ),
                    line=_line_of(python_code, match.start()),
                )
            )
    return out


_HTML_CAPTURE_MSG = (
    "processing script downloads documents but never captures page HTML "
    "(rule 14). Call result = await save_page_html(tab, out_dir, page_url) "
    "for the page where each PDF/doc was found and store "
    "Path(result['saved_path']).name as 'html_filename' (and the page URL "
    "as 'source_page_url') in EVERY save_record data dict that has a "
    "pdf_filename or supporting_filename. On SPA pages pass ready_selector "
    "naming the late-bound metadata element. Set 'html_filename': '' only "
    "when no HTML was captured for that row."
)


def _check_html_capture(python_code: str) -> list[LintFinding]:
    """Rule 14: a script that downloads documents must also capture page HTML."""
    download = re.search(
        r"\b(?:download_pdf_browser|download_pdf_curl_cffi|download_file_browser|download_file_curl_cffi)\s*\(",
        python_code,
    )
    if download is None:
        return []
    if re.search(r"\bsave_record\s*\(", python_code) is None:
        return []
    has_html_call = re.search(r"\bsave_page_html\s*\(", python_code) is not None
    has_html_key = re.search(r"['\"]html_filename['\"]", python_code) is not None
    if has_html_call and has_html_key:
        return []
    return [
        LintFinding(
            rule="14",
            severity="error",
            message=_HTML_CAPTURE_MSG,
            line=_line_of(python_code, download.start()),
        )
    ]


def _check_ready_selector(python_code: str) -> list[LintFinding]:
    """Reject heading/title ready_selectors — they bind with the initial shell and pass the gate early."""
    out: list[LintFinding] = []
    for match in re.finditer(r"\bsave_page_html\s*\(", python_code):
        call_text = _call_args(python_code, match.end() - 1)
        kw = re.search(r"ready_selector\s*=\s*([\"'])(.*?)\1", call_text)
        if not kw:
            continue
        selector = kw.group(2)
        is_heading = re.search(r"(?i)(^|[\s>+~])h[1-6](?=[.#\s>+~\]:]|$)", selector)
        is_titleish = re.search(r"(?i)[.#][\w-]*(title|header)[\w-]*", selector)
        if is_heading or is_titleish:
            out.append(
                LintFinding(
                    rule="14",
                    severity="error",
                    message=(
                        f"ready_selector {selector!r} names a page heading/title — headings render with the "
                        "initial shell and pass the gate BEFORE the late metadata XHR binds, so the captured "
                        "HTML still holds <!--anchor--> placeholders. Pass the late-bound metadata ITEM "
                        "metadata element instead — the same element your metadata-extraction tab.evaluate "
                        'queries (e.g. ready_selector=".document__credits metadata-item"). '
                        "Also forbidden: a class that matches server-rendered duplicates of the same "
                        'metadata elsewhere on the page (e.g. ".document__credits-item" matches the '
                        "static #original-text block on vLex) — it passes the gate instantly and the "
                        "capture keeps the <!--anchor--> placeholders."
                    ),
                    line=_line_of(python_code, match.start()),
                )
            )
    return out


def _check_file_size_key(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    pat = re.compile(r"""(?:\[\s*["']file_size["']\s*\]|\.get\(\s*["']file_size["']\s*\))""")
    for match in pat.finditer(python_code):
        out.append(
            LintFinding(
                rule="13",
                severity="error",
                message="result dicts have no file_size key; use size",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


def _check_zd_start(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for match in re.finditer(r"\bzd\.start\s*\(", python_code):
        out.append(
            LintFinding(
                rule="0",
                severity="warning",
                message="use start_browser() not zd.start() (will be rewritten but emit it directly)",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


def _check_tab_select_misuse(python_code: str) -> list[LintFinding]:
    """Flag ``tab.select(selector, value)`` — zendriver's select is query-only, not a dropdown setter."""
    out: list[LintFinding] = []
    for match in re.finditer(r"\btab\.select\s*\([^)]*,", python_code):
        out.append(
            LintFinding(
                rule="14",
                severity="error",
                message="tab.select(selector, value) does NOT set a dropdown — zendriver's select is query_selector only; use select_filter_value(tab, selector, value) or direct tab.get(url)",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


def _check_el_text_content(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    for match in re.finditer(r"\.text_content\s*\(", python_code):
        out.append(
            LintFinding(
                rule="4b",
                severity="error",
                message="el.text_content() does not exist in zendriver; use get_text from script_tools.dom_helpers",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


def _import_roots(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module] if node.module else []


def _check_http_imports(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for root in _import_roots(node):
            if root in _ALLOWED_URL_MODULES:
                continue
            if root.split(".")[0] in _HTTP_MODULES:
                out.append(LintFinding(rule="8", severity="error", message=_HTTP_MSG, line=node.lineno))
    return out


def _check_playwright_selectors(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    pat = re.compile(r":has-text\(|:text=|:visible|:has\(")
    for match in pat.finditer(python_code):
        out.append(
            LintFinding(
                rule="7",
                severity="error",
                message="Playwright-only selectors are rejected by CDP; use standard CSS only",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


def _is_iife_tail(python_code: str, start: int) -> bool:
    return bool(_EVAL_IIFE_TAIL.search(python_code[start : start + 60]))


def _check_evaluate_iife(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    pat = re.compile(r"""evaluate\s*\(\s*["']\s*(?:\(\s*)?\(\s*\)\s*=>\s*\{""")
    for match in pat.finditer(python_code):
        if not _is_iife_tail(python_code, match.end()):
            out.append(
                LintFinding(
                    rule="10",
                    severity="error",
                    message="tab.evaluate must be an expression or IIFE; a bare () => {} is never invoked",
                    line=_line_of(python_code, match.start()),
                )
            )
    return out


def _check_evaluate_args(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "evaluate":
            continue
        if len(node.args) > 1:
            out.append(
                LintFinding(
                    rule="10",
                    severity="error",
                    message="tab.evaluate takes only expression positionally; pass await_promise/return_by_value as keywords, interpolate other values into the string",
                    line=node.lineno,
                )
            )
    return out


def _check_evaluate_slice(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        awaited = node.value
        if not isinstance(awaited, ast.Await):
            continue
        call = awaited.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "evaluate":
            out.append(
                LintFinding(
                    rule="9",
                    severity="error",
                    message="tab.evaluate returns a Python dict/list, not a string — slicing it with [:N] raises; use str(result)[:N] or json.dumps(result)",
                    line=node.lineno,
                )
            )
    return out


def _check_retry_phase(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    imported = re.search(r"\bload_failed_downloads\b", python_code) is not None
    called = re.search(r"\bload_failed_downloads\s*\(", python_code) is not None
    if imported and not called:
        out.append(
            LintFinding(
                rule="8a",
                severity="error",
                message=(
                    "load_failed_downloads is imported but never called — main() MUST call "
                    "it in the rule-8a retry phase after the worker gather and before "
                    "browser.stop()"
                ),
                line=1,
            )
        )
    return out


def _check_gate_lock(python_code: str) -> list[LintFinding]:
    """Rule 15h: multi-tab SPA scripts must serialize the gate phase with an asyncio.Lock.

    Fires when a script uses multi-tab fanout (``asyncio.gather`` +
    ``bring_to_front``) but does NOT declare an ``asyncio.Lock`` and guard
    the foreground-dependent gate phase with ``async with``. Without the
    lock, concurrent per-tab ``bring_to_front()`` calls steal foreground
    from each other and N-1 tabs' SPA metadata never renders.
    """
    out: list[LintFinding] = []
    if "bring_to_front(" not in python_code:
        return out
    if "asyncio.gather(" not in python_code:
        return out
    has_lock_decl = re.search(r"asyncio\.Lock\s*\(", python_code) is not None
    has_async_with = re.search(r"async\s+with\s+\w+", python_code) is not None
    if has_lock_decl and has_async_with:
        return out
    out.append(
        LintFinding(
            rule="15h",
            severity="error",
            message=(
                "multi-tab script missing asyncio.Lock serialization of the "
                "metadata-gate phase — only ONE tab can be foreground at a time, "
                "so concurrent bring_to_front() calls steal foreground from each "
                "other and N-1 tabs' SPA metadata never renders (gate times out "
                "-> load_failed). Declare a shared `gate_lock = asyncio.Lock()` "
                "before the workers and wrap the navigate + bring_to_front + "
                "metadata-gate (+ retry) block in `async with gate_lock:`. Release "
                "before extraction/download so PDF I/O still parallelizes."
            ),
            line=None,
        )
    )
    return out


def _check_fanout(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    if "asyncio.gather(" not in python_code:
        return out
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    has_queue = "asyncio.Queue(" in python_code
    has_worker = any(isinstance(node, ast.AsyncFunctionDef) and "worker" in node.name for node in ast.walk(tree))
    if not (has_queue or has_worker):
        return out
    sem_match = re.search(r"asyncio\.Semaphore\(", python_code)
    modulo_match = re.search(r"idx\s*%\s*\w+|%\s*\w+_tabs", python_code)
    if sem_match and modulo_match:
        out.append(
            LintFinding(
                rule="15c",
                severity="error",
                message=(
                    "FORBIDDEN: asyncio.Semaphore with idx % N tab assignment — "
                    "slots release out of order, two tasks share one tab, "
                    "concurrent tab.get() invalidates element handles "
                    "(DOM.resolveNode -32000). Use one worker coroutine per "
                    "tab consuming a shared asyncio.Queue."
                ),
                line=_line_of(python_code, sem_match.start()),
            )
        )
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AsyncFunctionDef) and "worker" in node.name):
            continue
        try_ranges: list[tuple[int, int]] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Try):
                start = sub.lineno
                end = getattr(sub, "end_lineno", start)
                try_ranges.append((start, end))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Await):
                continue
            call = sub.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name != "process_document":
                continue
            in_try = any(start <= sub.lineno <= end for start, end in try_ranges)
            if not in_try:
                out.append(
                    LintFinding(
                        rule="15c",
                        severity="error",
                        message=(
                            "per-task try/except around process_document is "
                            "MANDATORY — without it one document's "
                            "TimeoutError propagates through asyncio.gather "
                            "and kills the whole run before the retry phase "
                            "recovers the row."
                        ),
                        line=sub.lineno,
                    )
                )
    if "bring_to_front(" not in python_code:
        out.append(
            LintFinding(
                rule="15h",
                severity="error",
                message=(
                    "multi-tab script missing tab.bring_to_front() — hidden "
                    "background tabs never fire IntersectionObserver/RAF so "
                    "SPA late-bound metadata never renders and the gate times "
                    "out. Call await tab.bring_to_front() before the metadata "
                    "gate in each per-document task."
                ),
                line=None,
            )
        )
    return out


def _check_bare_paths(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    pat = re.compile(r"""Path\(\s*["']downloads["']\s*\)|\./downloads""")
    for match in pat.finditer(python_code):
        out.append(
            LintFinding(
                rule="12",
                severity="error",
                message="output paths must be relative to __file__, not bare",
                line=_line_of(python_code, match.start()),
            )
        )
    return out


_BARE_HOST_CONCAT_MSG = (
    "never bare-concatenate a host onto an href (rule 13): build file_url "
    'with urljoin(base, quote(href, safe="/%?=&")) so leading whitespace and '
    "unsafe chars are percent-encoded, not embedded as raw spaces"
)


def _operand_has_href(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "href":
            return True
        if isinstance(sub, ast.Constant) and sub.value == "href":
            return True
    return False


def _check_bare_host_concat(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
            continue
        if _operand_has_href(node.left) or _operand_has_href(node.right):
            out.append(LintFinding(rule="13", severity="error", message=_BARE_HOST_CONCAT_MSG, line=node.lineno))
    return out


_CURL_CFFI_FUNCS = frozenset({"download_pdf_curl_cffi", "download_file_curl_cffi"})

_DOWNLOAD_ARG_ORDER_MSG = (
    "download_pdf_curl_cffi / download_file_curl_cffi take (url, save_path, tab) "
    "NOT (tab, url, save_path) — the browser variants download_pdf_browser / "
    "download_file_browser take (tab, url, save_path). Passing a tab as the first "
    "arg raises 'unhashable type: Tab' at runtime because the helper hashes the URL."
)


def _is_tab_like_arg(node: ast.AST) -> bool:
    """True when ``node`` is a variable named like a browser tab."""
    if isinstance(node, ast.Name):
        return "tab" in node.id.lower()
    if isinstance(node, ast.Attribute) and node.attr == "main_tab":
        return True
    return False


def _check_download_curl_cffi_args(python_code: str) -> list[LintFinding]:
    """Flag ``download_*_curl_cffi(tab, url, ...)`` — wrong argument order (tab first)."""
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in _CURL_CFFI_FUNCS:
            continue
        if node.args and _is_tab_like_arg(node.args[0]):
            out.append(
                LintFinding(
                    rule="8c",
                    severity="error",
                    message=_DOWNLOAD_ARG_ORDER_MSG,
                    line=node.lineno,
                )
            )
    return out


def _bad_self_root(root: str) -> bool:
    return root == "browser_agent" or root.startswith("browser_agent.")


def _check_self_contained(python_code: str) -> list[LintFinding]:
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if any(_bad_self_root(root) for root in _import_roots(node)):
            out.append(LintFinding(rule="5", severity="error", message=_SELF_MSG, line=node.lineno))
    return out


def _check_script_tools_package_import(python_code: str) -> list[LintFinding]:
    """Flag ``from script_tools import X`` and non-existent ``script_tools.X`` modules."""
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    valid_modules = frozenset(
        {
            "start_browser",
            "save_record",
            "save_page_html",
            "pdf_download",
            "page_wait",
            "dom_helpers",
            "form_helpers",
            "discover_links",
            "discovered_links_store",
            "_file_utils",
            "run_config",
        }
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "script_tools":
            out.append(LintFinding(rule="5b", severity="error", message=_SCRIPT_TOOLS_PKG_MSG, line=node.lineno))
        elif node.module and node.module.startswith("script_tools."):
            submod = node.module.split(".", 1)[1]
            if submod not in valid_modules:
                out.append(
                    LintFinding(
                        rule="0",
                        severity="error",
                        message=f"script_tools.{submod} does not exist. Valid modules: {', '.join(sorted(valid_modules))}. Use 'from script_tools.page_wait import prepare_page_wait' (module=page_wait), not 'from script_tools.prepare_page_wait import ...'.",
                        line=node.lineno,
                    )
                )
    return out


def _check_direct_zendriver_import(python_code: str) -> list[LintFinding]:
    """Flag ``from zendriver import ...`` and ``import zendriver`` in emitted scripts.

    Emitted scripts must use ``from script_tools.start_browser import start_browser``
    and ``from script_tools.page_wait import ...`` — never import zendriver
    directly. ``zendriver.cdp`` is used internally by script_tools helpers, but
    emitted scripts have no need for it.
    """
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        roots = _import_roots(node)
        for root in roots:
            if root and root.split(".")[0] == "zendriver":
                out.append(
                    LintFinding(
                        rule="0",
                        severity="error",
                        message="never import zendriver directly; use 'from script_tools.start_browser import start_browser' and other script_tools helpers",
                        line=node.lineno,
                    )
                )
    return out


# Zendriver-specific rules — violations indicate the agent does not
# understand zendriver's API surface (browser launcher, CDP-only
# selectors, evaluate calling convention, helper return shapes).
# Separated from general lint rules so the driver can log them distinctly.
_ZENDRIVER_RULES: frozenset[str] = frozenset(
    {
        "2",  # hand-written discovery loop instead of discover_links helper
        "0",  # zd.start() vs start_browser()
        "4b",  # el.text_content() is a Playwright method, not zendriver
        "7",  # Playwright-only selectors (CDP rejects them)
        "8",  # HTTP libs instead of tab.get()
        "10",  # tab.evaluate calling convention
        "11",  # await save_record (sync)
        "13",  # file_size vs size key
        "9",  # tab.evaluate returns a dict/list, not a slicable string
        "8c",  # download_*_curl_cffi called with tab as first arg (wrong order)
    }
)


def _is_zendriver_rule(rule: str) -> bool:
    return rule in _ZENDRIVER_RULES


_ZENDRIVER_RULE_NAMES: dict[str, str] = {
    "0": "browser launcher — uses zd.start() instead of start_browser()",
    "4b": "element handle — used el.text_content() which is not a zendriver method (use get_text)",
    "7": "selectors — uses Playwright-only pseudo-selectors rejected by CDP",
    "8": "HTTP client — uses raw HTTP lib instead of zendriver tab.get()",
    "2": "discovery loop — hand-written scroll/load-more loop instead of discover_links helper",
    "11": "save_record — awaited a synchronous helper (TypeError at runtime)",
    "13": "result shape — uses file_size key instead of size",
    "8c": "download helper args — called download_pdf_curl_cffi/download_file_curl_cffi with tab first; curl_cffi variants take (url, save_path, tab), browser variants take (tab, url, save_path)",
}


def _check_skeleton(python_code: str) -> list[LintFinding]:
    """Enforce the fixed script skeleton (rule 1): trailer, start_browser first, finally-stop."""
    findings: list[LintFinding] = []
    trailer_ok = python_code.rstrip().endswith('if __name__ == "__main__":\n    asyncio.run(main())')
    if not trailer_ok:
        findings.append(
            LintFinding(
                rule="1",
                severity="error",
                message='script MUST end with exactly: if __name__ == "__main__": then asyncio.run(main())',
                line=None,
            )
        )
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return findings
    main_fn = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main":
            main_fn = node
            break
    if main_fn is None:
        findings.append(
            LintFinding(
                rule="1",
                severity="error",
                message="missing top-level async def main()",
                line=None,
            )
        )
        return findings
    if not main_fn.body:
        findings.append(
            LintFinding(
                rule="1",
                severity="error",
                message="first statement of main() MUST be browser = await start_browser()",
                line=main_fn.lineno,
            )
        )
    else:
        first = main_fn.body[0]
        is_start = (
            isinstance(first, ast.Assign)
            and isinstance(first.value, ast.Await)
            and isinstance(first.value.value, ast.Call)
            and isinstance(first.value.value.func, ast.Name)
            and first.value.value.func.id == "start_browser"
        )
        if not is_start:
            findings.append(
                LintFinding(
                    rule="1",
                    severity="error",
                    message="first statement of main() MUST be browser = await start_browser()",
                    line=first.lineno if hasattr(first, "lineno") else main_fn.lineno,
                )
            )
    has_finally_stop = False
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                if (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Await)
                    and isinstance(stmt.value.value, ast.Call)
                    and isinstance(stmt.value.value.func, ast.Attribute)
                    and stmt.value.value.func.attr == "stop"
                ):
                    has_finally_stop = True
                    break
        if has_finally_stop:
            break
    if not has_finally_stop:
        findings.append(
            LintFinding(
                rule="1",
                severity="error",
                message="browser.stop() MUST be awaited inside a finally: block so the browser closes on errors",
                line=None,
            )
        )
    return findings


def _loop_has_discovery_signals(body: list[ast.stmt]) -> bool:
    """True when a loop body has scroll + link-read + growth/termination signals."""
    has_scroll = has_linkread = has_growth = False
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "scrollTo" in node.value or "scrollHeight" in node.value:
                has_scroll = True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "querySelectorAll" in node.value:
                has_linkread = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"get_links", "_count_links"}:
                has_linkread = True
        if isinstance(node, ast.Name) and node.id in {"stable", "prev"}:
            has_growth = True
    return has_scroll and has_linkread and has_growth


def _check_handwritten_discovery(python_code: str) -> list[LintFinding]:
    """Rule 2: flag a hand-written discovery loop when no discover_links call exists."""
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return []
    if "discover_links(" not in python_code:
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While, ast.AsyncFor)) and _loop_has_discovery_signals(node.body):
                return [
                    LintFinding(
                        rule="2",
                        severity="error",
                        message="hand-written discovery loop forbidden — call discover_links(tab, ...) from script_tools.discover_links (rule 2)",
                        line=node.lineno,
                    )
                ]
    return []


_DOC_EXTENSIONS = ("pdf", "doc", "docx", "rtf", "xls", "xlsx", "ppt", "pptx")
_HREF_EXT_RE = re.compile(r"href\$=(['\"])(\.[A-Za-z]{2,5})\1(\s+i)?\]")


def _is_scope_name(node: ast.AST) -> bool:
    """True when ``node`` looks like a scope variable (bare Name, not a str)."""
    return isinstance(node, ast.Name)


def _joined_str_text(node: ast.JoinedStr) -> str:
    """Concatenate the literal Constant parts of an f-string into a string."""
    parts: list[str] = []
    for p in node.values:
        if isinstance(p, ast.Constant) and isinstance(p.value, str):
            parts.append(p.value)
    return "".join(parts)


def _selector_has_unscoped_comma(selector: str) -> bool:
    """Compound ``href$=`` selector split by comma without ``:is(``/``:where(``."""
    if "href$=" not in selector or "," not in selector:
        return False
    return ":is(" not in selector and ":where(" not in selector


def _check_unscoped_compound_selector(python_code: str) -> list[LintFinding]:
    """Rule 16: scope-prefixed compound CSS selector — comma unscopes later parts."""
    out: list[LintFinding] = []
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        selector: str | None = None
        scope: str | None = None
        line: int | None = None
        if isinstance(node, ast.JoinedStr):
            has_scope = any(isinstance(v, ast.FormattedValue) and _is_scope_name(v.value) for v in node.values)
            text = _joined_str_text(node)
            if has_scope and _selector_has_unscoped_comma(text):
                selector = text
                scope = next(
                    (v.value.id for v in node.values if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)),
                    "scope",
                )
                line = node.lineno
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = node.left, node.right
            if isinstance(left, ast.Name) and isinstance(right, ast.Constant):
                text = right.value
                if isinstance(text, str) and _selector_has_unscoped_comma(text):
                    selector = text
                    scope = left.id
                    line = node.lineno
            elif isinstance(right, ast.Name) and isinstance(left, ast.Constant):
                text = left.value
                if isinstance(text, str) and _selector_has_unscoped_comma(text):
                    selector = text
                    scope = right.id
                    line = node.lineno
        if selector is not None and scope is not None and line is not None:
            inner = selector.strip()
            out.append(
                LintFinding(
                    rule="16",
                    severity="error",
                    message=(
                        f"compound CSS selector '{inner}' is prefixed with a scope "
                        f"but the comma splits it into independent selectors — only the first "
                        f"part is scoped to {scope}. Wrap the compound selector in :is() so "
                        f'the scope applies to all parts: f"{{{scope}}} :is({inner})".'
                    ),
                    line=line,
                )
            )
    # Two-step constant pattern: a module-level str constant with the
    # compound shape, later prefixed by a scope in an f-string/concat.
    compound_consts: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    if _selector_has_unscoped_comma(node.value.value):
                        compound_consts[tgt.id] = node.value.value
    for node in ast.walk(tree):
        ref_name: ast.Name | None = None
        scope_name: str | None = None
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name):
                    if v.value.id in compound_consts:
                        ref_name = v.value
                    elif scope_name is None:
                        scope_name = v.value.id
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = node.left, node.right
            name_node = left if isinstance(left, ast.Name) else right
            if isinstance(name_node, ast.Name):
                if name_node.id in compound_consts:
                    ref_name = name_node
                elif scope_name is None:
                    scope_name = name_node.id
        if ref_name is not None and scope_name is not None and hasattr(node, "lineno"):
            selector = compound_consts[ref_name.id]
            out.append(
                LintFinding(
                    rule="16",
                    severity="error",
                    message=(
                        f"compound CSS selector '{selector.strip()}' is prefixed with a scope "
                        f"but the comma splits it into independent selectors — only the first "
                        f"part is scoped to {scope_name}. Wrap the compound selector in :is() "
                        f'so the scope applies to all parts: f"{{{scope_name}}} :is({selector.strip()})".'
                    ),
                    line=getattr(node, "lineno", None),
                )
            )
    return out


def _check_case_sensitive_extension_selector(python_code: str) -> list[LintFinding]:
    """Rule 17: ``a[href$='.doc']`` without the CSS ``i`` flag misses ``.DOC``."""
    out: list[LintFinding] = []
    seen: set[int] = set()
    for match in _HREF_EXT_RE.finditer(python_code):
        quote, ext, iflag = match.group(1), match.group(2).lower(), match.group(3)
        if iflag:
            continue
        upper = ext.upper()
        span_text = python_code[max(0, match.start() - 200) : match.end() + 200]
        both_cases = f"href$={quote}{upper}{quote}" in span_text
        if both_cases:
            continue
        line = _line_of(python_code, match.start())
        if line in seen:
            continue
        seen.add(line)
        out.append(
            LintFinding(
                rule="17",
                severity="warning",
                message=(
                    f"CSS $= is case-sensitive: a[href$={quote}{ext}{quote}] misses "
                    f"{upper} links. Use the CSS i flag for case-insensitive matching: "
                    f"a[href$={quote}{ext}{quote} i]."
                ),
                line=line,
            )
        )
    return out


class EmittedScriptLinter:
    """Lint the RAW LLM python_code (before emit transforms)."""

    def __init__(self) -> None:
        self._DISCOVERY_CHECKS = (
            _check_syntax,
            _check_skeleton,
            _check_http_imports,
            _check_playwright_selectors,
            _check_el_text_content,
            _check_script_tools_package_import,
            _check_direct_zendriver_import,
            _check_evaluate_iife,
            _check_evaluate_args,
            _check_evaluate_slice,
            _check_discovery_save_link,
            _check_bare_paths,
            _check_self_contained,
            _check_tab_select_misuse,
            _check_handwritten_discovery,
            _check_unscoped_compound_selector,
            _check_case_sensitive_extension_selector,
        )
        self._PROCESSING_CHECKS = (
            _check_syntax,
            _check_skeleton,
            _check_ready_selector,
            _check_save_record,
            _check_tab_select_misuse,
            _check_download_status,
            _check_supporting_status,
            _check_html_capture,
            _check_http_imports,
            _check_playwright_selectors,
            _check_el_text_content,
            _check_evaluate_iife,
            _check_evaluate_args,
            _check_evaluate_slice,
            _check_bare_paths,
            _check_bare_host_concat,
            _check_download_curl_cffi_args,
            _check_self_contained,
            _check_script_tools_package_import,
            _check_direct_zendriver_import,
            _check_fanout,
            _check_gate_lock,
            _check_handwritten_discovery,
            _check_unscoped_compound_selector,
            _check_case_sensitive_extension_selector,
        )

    def lint(self, python_code: str, kind: str = "processing") -> list[LintFinding]:
        findings: list[LintFinding] = []
        checks = self._DISCOVERY_CHECKS if kind == "discovery" else self._PROCESSING_CHECKS
        for check in checks:
            findings.extend(check(python_code))
        return findings

    @staticmethod
    def zendriver_findings(findings: list[LintFinding]) -> list[LintFinding]:
        """Return only the findings that indicate zendriver API misunderstanding."""
        return [f for f in findings if _is_zendriver_rule(f.rule)]

    @staticmethod
    def general_findings(findings: list[LintFinding]) -> list[LintFinding]:
        """Return findings that are NOT zendriver-specific (syntax, convention, paths)."""
        return [f for f in findings if not _is_zendriver_rule(f.rule)]

    @staticmethod
    def describe_zendriver_finding(finding: LintFinding) -> str:
        """Return a human-readable description of the zendriver concept the agent got wrong."""
        return _ZENDRIVER_RULE_NAMES.get(finding.rule, finding.message)

    @staticmethod
    def format_zendriver_summary(findings: list[LintFinding], path: Path | None = None) -> str:
        """Format zendriver findings as a multi-line warning block (or "" when none)."""
        zd = EmittedScriptLinter.zendriver_findings(findings)
        if not zd:
            return ""
        prefix = f"[EMIT ZD-ERROR] {path}: " if path is not None else "[ZD-ERROR] "
        lines: list[str] = []
        for f in zd:
            loc = f" line {f.line}" if f.line is not None else ""
            concept = EmittedScriptLinter.describe_zendriver_finding(f)
            lines.append(f"{prefix}rule={f.rule}{loc} — {concept}: {f.message}")
        return "\n".join(lines)

    @staticmethod
    def format_zendriver_gaps(findings: list[LintFinding]) -> str:
        """Format the zendriver knowledge-gaps summary line (or "" when none)."""
        zd = EmittedScriptLinter.zendriver_findings(findings)
        if not zd:
            return ""
        gaps = "; ".join(sorted({EmittedScriptLinter.describe_zendriver_finding(f) for f in zd}))
        return f"zendriver knowledge gaps: {len(zd)} rule violation(s) — agent does not understand: {gaps}"
