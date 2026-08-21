"""Zendriver runtime error patterns that indicate an agent misunderstanding of
zendriver's API surface.

Each entry is ``(pattern, label, fix)``:

* ``pattern`` — a substring scanned against a script's combined output.
* ``label`` — a short tag used in operator log lines.
* ``fix`` — a self-contained description that already carries the corrective
  fix, so the diagnosis returned to the agent is actionable.

Shared by the ``run_validation_script`` tool (which returns a ``# DIAGNOSIS``
block to the agent) and the smoke tester (which surfaces the same gaps in
operator logs). A single copy prevents the three tables that previously
drifted in parallel from diverging again.
"""

from __future__ import annotations

# The complete set of script_tools modules copied beside an emitted script.
# Single source of truth: the ModuleNotFoundError diagnosis below and the
# static hallucinated-import pre-check both derive from this tuple.
SCRIPT_TOOLS_MODULES: tuple[str, ...] = (
    "_file_utils",
    "discover_links",
    "discovered_links_store",
    "dom_helpers",
    "extract_fields",
    "form_helpers",
    "page_wait",
    "pdf_download",
    "save_page_html",
    "save_record",
    "start_browser",
    "text_utils",
)

_SCRIPT_TOOLS_MODULE_LIST = ", ".join(f"script_tools.{m}" for m in SCRIPT_TOOLS_MODULES)

ZD_RUNTIME_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        "tab.evaluate",
        "evaluate() missing 1 required positional argument",
        'tab.evaluate called without an expression argument. Fix: pass a JS expression string as the first argument, e.g. await tab.evaluate("document.title").',
    ),
    (
        "can't be used in 'await' expression",
        "awaited sync value",
        "You awaited a synchronous VALUE instead of a coroutine. zendriver element properties (el.text, el.text_all, el.attrs, el.id) and the SYNC helpers save_record/load_discovered_links/mark_link_processed return plain values — never await them (rule 4/11). Fix: await get_text(el, tab) (rule 0) for text, read attributes without await via el.attrs.get('href'), and use await el.apply('(el) => el.textContent') for full subtree text.",
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
        "ImportError",
        "Wrong import name. Fix: import only from script_tools.<module> (rule 0) — modules: save_record (save_record, load_failed_downloads), save_page_html (save_page_html), pdf_download (download_pdf_curl_cffi, download_pdf_browser), page_wait (wait_for_page_ready, wait_for_anchors, prepare_page_wait), start_browser (start_browser).",
    ),
    (
        "ModuleNotFoundError: No module named '",
        "ModuleNotFoundError",
        "Missing module. Fix: only zendriver, asyncio, stdlib, and script_tools.* are available (rule 0/5); "
        "the script_tools/ folder is copied beside the script at emit time. The ONLY available script_tools "
        f"modules are: {_SCRIPT_TOOLS_MODULE_LIST} — no other script_tools modules exist. Note: extract_rows, "
        "extract_links, and extract_fields are all FUNCTIONS inside script_tools.extract_fields — there is no "
        "script_tools.extract_rows/extract_links module; import them from script_tools.extract_fields.",
    ),
    (
        "AttributeError: '",
        "AttributeError",
        "Wrong method/property on a zendriver element. Fix: read attributes with el.attrs.get('href') (sync dict — never await it) and full text with await el.apply('(el) => el.textContent'); use the get_text/get_attr helpers (rule 0) instead of hand-rolling, and NEVER el.text_content().",
    ),
    (
        "TypeError: ",
        "TypeError",
        "Wrong argument type — read the last traceback line: it names the exact object/argument that was wrong. Common zendriver causes are awaited sync values (see the \"can't be used in 'await' expression\" diagnosis) or a wrong positional/keyword. Fix the exact call named in the traceback.",
    ),
    (
        "asyncio.run() cannot be called from a running event loop",
        "asyncio.run error",
        "asyncio.run() inside a running loop. Fix: the script's top-level is asyncio.run(main()) — never call asyncio.run() from inside an async function.",
    ),
    (
        "unhashable type: 'Tab'",
        "download arg order",
        "download_pdf_curl_cffi / download_file_curl_cffi called with a Tab as the first argument — the curl_cffi helpers take (url, save_path, tab), NOT (tab, url, save_path). Fix: pass the URL first, e.g. download_pdf_curl_cffi(file_url, out_dir, wtab).",
    ),
]
