from __future__ import annotations

import ast
import re

from browser_agent.domain.lint_finding import LintFinding

_HTTP_MODULES = frozenset({"requests", "httpx", "aiohttp", "urllib", "urllib3"})
_HTTP_MSG = "no HTTP libraries; use tab.get() and vendored download helpers"
_SELF_MSG = "script must be self-contained; only browser_agent.runtime_helpers is allowed (stripped at emit time)"
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
    return root.startswith("browser_agent.") and root != "browser_agent.runtime_helpers"


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


class EmittedScriptLinter:
    """Lint the RAW LLM python_code (before emit transforms)."""

    def __init__(self) -> None:
        self._checks = (
            _check_syntax,
            _check_save_record,
            _check_file_size_key,
            _check_zd_start,
            _check_http_imports,
            _check_playwright_selectors,
            _check_evaluate_iife,
            _check_bare_paths,
            _check_self_contained,
        )

    def lint(self, python_code: str) -> list[LintFinding]:
        findings: list[LintFinding] = []
        for check in self._checks:
            findings.extend(check(python_code))
        return findings
