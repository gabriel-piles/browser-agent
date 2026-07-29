from __future__ import annotations

import ast
import re

from browser_agent.domain.lint_finding import LintFinding

_HTTP_MODULES = frozenset({"requests", "httpx", "aiohttp", "urllib", "urllib3"})
_HTTP_MSG = "no HTTP libraries; use tab.get() and script_tools download helpers"
_SELF_MSG = (
    "script imports must be stdlib, zendriver, or script_tools.* (real modules copied beside the script at emit time)"
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
        if any(root.split(".")[0] in _HTTP_MODULES for root in _import_roots(node)):
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


# Zendriver-specific rules — violations indicate the agent does not
# understand zendriver's API surface (browser launcher, CDP-only
# selectors, evaluate calling convention, helper return shapes).
# Separated from general lint rules so the driver can log them distinctly.
_ZENDRIVER_RULES: frozenset[str] = frozenset(
    {
        "0",  # zd.start() vs start_browser()
        "7",  # Playwright-only selectors (CDP rejects them)
        "8",  # HTTP libs instead of tab.get()
        "10",  # tab.evaluate calling convention
        "11",  # await save_record (sync)
        "13",  # file_size vs size key
    }
)


def _is_zendriver_rule(rule: str) -> bool:
    return rule in _ZENDRIVER_RULES


_ZENDRIVER_RULE_NAMES: dict[str, str] = {
    "0": "browser launcher — uses zd.start() instead of start_browser()",
    "7": "selectors — uses Playwright-only pseudo-selectors rejected by CDP",
    "8": "HTTP client — uses raw HTTP lib instead of zendriver tab.get()",
    "10": "tab.evaluate — wrong calling convention (extra positional args or bare arrow function)",
    "11": "save_record — awaited a synchronous helper (TypeError at runtime)",
    "13": "result shape — uses file_size key instead of size",
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
                message="first statement of main() MUST be browser = await start_browser(headless=False)",
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
                    message="first statement of main() MUST be browser = await start_browser(headless=False)",
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


class EmittedScriptLinter:
    """Lint the RAW LLM python_code (before emit transforms)."""

    def __init__(self) -> None:
        self._checks = (
            _check_syntax,
            _check_skeleton,
            _check_save_record,
            _check_zd_start,
            _check_download_status,
            _check_http_imports,
            _check_playwright_selectors,
            _check_evaluate_iife,
            _check_evaluate_args,
            _check_bare_paths,
            _check_self_contained,
        )

    def lint(self, python_code: str) -> list[LintFinding]:
        findings: list[LintFinding] = []
        for check in self._checks:
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
