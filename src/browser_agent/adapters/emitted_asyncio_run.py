"""Neutralize ``asyncio.run(...)`` calls in emitted validation code.

The in-process validation runner executes the LLM's ``main()``
directly inside the agent's already-running event loop (see
:mod:`browser_agent.adapters.execution.in_process_script_runner_adapter`).
The LLM is told to write ``asyncio.run(main())`` at the bottom of the
script (see :mod:`browser_agent.use_cases.system_prompt`), and the
runner strips the trailing
``if __name__ == "__main__": asyncio.run(main())`` trailer via a regex.

That regex only matches the exact canonical trailer. When the LLM
emits a *different* shape — ``asyncio.run(main())`` with extra
whitespace, a trailing comment, the call placed at module top level
without the ``if __name__`` guard, or ``from asyncio import run``
followed by ``run(main())`` — the regex leaves the
``asyncio.run(...)`` in place. Executing it inside the running loop
raises ``RuntimeError: asyncio.run() cannot be called from a running
event loop``. Worse, because zendriver's websocket listener is a
task on the same loop, the failed ``asyncio.run`` corrupts the loop's
task graph and the next validation attempt dies with
``RuntimeError: ... got Future ... attached to a different loop`` —
the crash seen in the IACHR run.

This transform uses :func:`ast.parse` to handle every shape robustly:

* The canonical trailer
  ``if __name__ == "__main__": asyncio.run(main())`` is dead code in
  validation (the runner sets ``__name__ = "__validation__"``), so the
  whole ``if __name__ == "__main__":`` block is removed.
* A bare top-level ``asyncio.run(main())`` statement (no guard) is
  removed too — the runner always awaits ``main()`` itself, so the
  LLM's top-level ``asyncio.run`` is redundant.
* ``asyncio.run(coro)`` / ``run(coro)`` calls *inside* a function
  (e.g. the LLM runs a helper coroutine via ``asyncio.run`` inside
  ``main``) are rewritten to ``await coro`` so they compose with the
  running loop. ``await`` is legal there because the enclosing
  function is ``async def``.

AST matching is robust to whitespace, comments, and placement; the
regex trailer strip in the runner stays as a fast path for the
canonical form. If the code has no ``asyncio.run`` / qualifying
``run`` call and no ``if __name__ == "__main__"`` block, this is a
no-op (returns the input unchanged).
"""

from __future__ import annotations

import ast


def with_emitted_asyncio_run(python_code: str) -> str:
    """Neutralize every ``asyncio.run(...)`` / qualifying ``run(...)`` in ``python_code``.

    Returns the input unchanged when there is nothing to rewrite.
    """
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return python_code
    has_from_import_run = _has_from_asyncio_import_run(tree)
    rewriter = _AsyncioRunRewriter(has_from_import_run)
    new_tree = rewriter.visit(tree)
    if not rewriter.changed:
        return python_code
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def _has_from_asyncio_import_run(tree: ast.Module) -> bool:
    """True if the module has ``from asyncio import run`` (any aliasing)."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for alias in node.names:
                if alias.name == "run":
                    return True
    return False


class _AsyncioRunRewriter(ast.NodeTransformer):
    """Remove top-level asyncio.run trailers; await asyncio.run inside functions."""

    def __init__(self, has_from_import_run: bool) -> None:
        self.has_from_import_run: bool = has_from_import_run
        self.changed: bool = False
        # When True we are inside an ``async def`` (or ``def``) body, so
        # ``await`` is legal for in-function ``asyncio.run`` rewrites.
        self._inside_function: bool = False

    def visit_Module(self, node: ast.Module) -> ast.AST:  # noqa: N802
        kept: list[ast.stmt] = []
        for stmt in node.body:
            if _is_dunder_main_block(stmt):
                self.changed = True
                continue
            if _is_top_level_asyncio_run_stmt(stmt, self.has_from_import_run):
                self.changed = True
                continue
            kept.append(stmt)
        node.body = kept
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:  # noqa: N802
        self._inside_function = True
        self.generic_visit(node)
        self._inside_function = False
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # noqa: N802
        self._inside_function = True
        self.generic_visit(node)
        self._inside_function = False
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        if not self._inside_function:
            return node
        if _is_asyncio_run_call(node) or (self.has_from_import_run and _is_bare_run_call(node)):
            self.changed = True
            return ast.Await(value=node.args[0])
        return node


def _is_dunder_main_block(stmt: ast.stmt) -> bool:
    """True when ``stmt`` is an ``if __name__ == "__main__":`` block."""
    if not isinstance(stmt, ast.If):
        return False
    test = stmt.test
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return _is_name_main(left, right) or _is_name_main(right, left)


def _is_name_main(left: ast.expr, right: ast.expr) -> bool:
    """True when ``left`` is ``__name__`` and ``right`` is the string ``"__main__"``."""
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    )


def _is_top_level_asyncio_run_stmt(stmt: ast.stmt, has_from_import_run: bool) -> bool:
    """True when ``stmt`` is a bare ``asyncio.run(coro)`` / ``run(coro)`` statement."""
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    return _is_asyncio_run_call(call) or (has_from_import_run and _is_bare_run_call(call))


def _is_asyncio_run_call(node: ast.Call) -> bool:
    """True when ``node`` is ``asyncio.run(...)`` on the bare ``asyncio`` name."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "run":
        return False
    value = func.value
    return isinstance(value, ast.Name) and value.id == "asyncio" and _single_arg(node)


def _is_bare_run_call(node: ast.Call) -> bool:
    """True when ``node`` is a bare ``run(...)`` (after ``from asyncio import run``)."""
    func = node.func
    return isinstance(func, ast.Name) and func.id == "run" and _single_arg(node)


def _single_arg(node: ast.Call) -> bool:
    """True when ``node`` has exactly one positional arg and no star-args."""
    return len(node.args) == 1 and not node.keywords and all(not isinstance(a, ast.Starred) for a in node.args)
