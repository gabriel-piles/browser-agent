"""Catch deterministic validation-script bugs before execution.

Introspects ``browser_agent.script_tools`` to mirror the vendored rule-0
helper signatures, then runs pyflakes for undefined names plus AST checks
for helper-call misuse. Limitation: the len()/unpacking-on-None check only
inspects DIRECT call sites — no dataflow past a variable assignment.
"""

from __future__ import annotations

import ast
import difflib
import importlib
import inspect

import pyflakes.checker

_HELPER_MODULES = (
    "start_browser",
    "save_record",
    "save_page_html",
    "pdf_download",
    "page_wait",
    "dom_helpers",
    "form_helpers",
    "discover_links",
    "extract_fields",
    "discovered_links_store",
    "text_utils",
)

_registry: dict[str, object] | None = None


def precheck(python_code: str) -> str:
    """Return a fix message for a deterministic bug, or "" when clean."""
    try:
        return _precheck_impl(python_code)
    except Exception:
        return ""


def _precheck_impl(python_code: str) -> str:
    for check in (
        _check_undefined,
        _check_arity,
        _check_await_sync,
        _check_async_unawaited,
        _check_len_on_none,
    ):
        msg = check(python_code)
        if msg:
            return msg
    return ""


def _get_registry() -> dict[str, object]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def _build_registry() -> dict[str, object]:
    registry: dict[str, object] = {}
    for mod_name in _HELPER_MODULES:
        try:
            mod = importlib.import_module(f"browser_agent.script_tools.{mod_name}")
        except Exception:
            continue
        registry.update(_module_entries(mod))
    return registry


def _module_entries(mod) -> dict[str, object]:
    entries: dict[str, object] = {}
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        obj = getattr(mod, attr)
        if not callable(obj) or inspect.isclass(obj):
            continue
        try:
            sig = inspect.signature(obj)
        except (ValueError, TypeError):
            continue
        entries[attr] = _entry(obj, sig)
    return entries


def _entry(obj, sig) -> dict[str, object]:
    params = list(sig.parameters.values())
    return {
        "signature": str(sig),
        "is_async": inspect.iscoroutinefunction(obj),
        "returns_none": _returns_none(obj, sig),
        "names": [p.name for p in params],
        "required": _required_count(params),
        "pos_count": _pos_count(params),
        "variadic": _is_variadic(params),
    }


def _required_count(params) -> int:
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return sum(1 for p in params if p.default is inspect.Parameter.empty and p.kind in kinds)


def _pos_count(params) -> int:
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return sum(1 for p in params if p.kind in kinds)


def _is_variadic(params) -> bool:
    return any(p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) for p in params)


def _returns_none(obj, sig) -> bool:
    ann = sig.return_annotation
    if ann is not inspect.Signature.empty:
        return ann is None
    try:
        tree = ast.parse(inspect.getsource(obj))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return False
        if isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                return False
    return True


def _check_undefined(python_code: str) -> str:
    from pyflakes import messages as _m

    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    checker = pyflakes.checker.Checker(tree, "<script>")
    names = [m.message_args[0] for m in checker.messages if isinstance(m, _m.UndefinedName)]
    if not names:
        return ""
    registry = _get_registry()
    return "\n".join(_undefined_message(n, registry) for n in names[:3])


def _undefined_message(name: str, registry: dict[str, object]) -> str:
    match = difflib.get_close_matches(name, list(registry), n=1, cutoff=0.6)
    if match:
        return f"name '{name}' is not defined. Vendored helpers: did you mean '{match[0]}'? Full signatures are in rule 0."
    return f"name '{name}' is not defined — every name must come from an import or a def in the script (rule 0 signatures)."


def _check_arity(python_code: str) -> str:
    registry = _get_registry()
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        entry = registry.get(node.func.id)
        if entry is None or entry["variadic"]:
            continue
        if _arity_bad(entry, node):
            return _arity_message(node.func.id, entry)
    return ""


def _arity_message(name: str, entry: dict[str, object]) -> str:
    return f"{name} expects {name}{entry['signature']} (rule 0) — fix the arguments."


def _arity_bad(entry, node) -> bool:
    if any(isinstance(a, ast.Starred) for a in node.args):
        return False
    if any(kw.arg is None for kw in node.keywords):
        return False
    pos = len(node.args)
    if pos < entry["required"] or pos > entry["pos_count"]:
        return True
    given = {kw.arg for kw in node.keywords}
    return not given.issubset(set(entry["names"]))


def _check_await_sync(python_code: str) -> str:
    registry = _get_registry()
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
            continue
        name = _callee_name(node.value.func)
        entry = registry.get(name) if name else None
        if entry and not entry["is_async"]:
            return f"await {name}(...) — it is synchronous (rule 0); drop the await."
    return ""


def _check_async_unawaited(python_code: str) -> str:
    registry = _get_registry()
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        name = _callee_name(node.value.func)
        entry = registry.get(name) if name else None
        if entry and entry["is_async"]:
            return f"{name}(...) is async — its result must be awaited (rule 0)."
    return ""


def _callee_name(func) -> str | None:
    return func.id if isinstance(func, ast.Name) else None


def _check_len_on_none(python_code: str) -> str:
    registry = _get_registry()
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        name = _len_none_target(node, registry)
        if name:
            return _none_msg(name)
    return ""


def _len_none_target(node, registry) -> str | None:
    if isinstance(node, ast.Call) and _is_len(node.func):
        if node.args:
            return _none_helper(_unwrap_await(node.args[0]), registry)
    if isinstance(node, ast.Assign) and _is_tuple_target(node):
        return _none_helper(node.value, registry)
    return None


def _none_helper(value, registry) -> str | None:
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        entry = registry.get(value.func.id)
        if entry and entry["returns_none"]:
            return value.func.id
    return None


def _is_len(func) -> bool:
    return isinstance(func, ast.Name) and func.id == "len"


def _is_tuple_target(node) -> bool:
    return bool(node.targets) and isinstance(node.targets[0], ast.Tuple)


def _unwrap_await(node):
    return node.value if isinstance(node, ast.Await) else node


def _none_msg(name: str) -> str:
    return (
        f"{name}(...) returns None (rule 0) — len()/unpacking on it will raise "
        "TypeError: object of type 'NoneType' has no len()."
    )
