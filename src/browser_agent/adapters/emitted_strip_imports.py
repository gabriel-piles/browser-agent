"""Strip ``browser_agent.runtime_helpers`` imports from emitted code.

The LLM is told to write::

    from browser_agent.runtime_helpers import (
        save_record, save_page_html, ...
    )

so it sees real typed signatures while generating the script. That import
is a development-time anchor only — the final script MUST be
self-contained (no project imports). This module removes every
``browser_agent.runtime_helpers`` import (single-line, multi-line, and
bare ``import browser_agent.runtime_helpers`` forms) from the LLM's
source so the subsequent vendored-block transforms can prepend the real
implementations without a name clash.

If the code has no such import, the function is a no-op (returns the
input unchanged) — backward compatible with scripts that predate the
import-based contract.
"""

from __future__ import annotations

import ast
import re as _re

_TARGET_MODULE = "browser_agent.runtime_helpers"


def with_emitted_strip_imports(python_code: str) -> str:
    """Remove every ``browser_agent.runtime_helpers`` import from ``python_code``.

    Uses :func:`ast.parse` to locate ``ast.ImportFrom`` nodes whose
    ``module`` is ``browser_agent.runtime_helpers`` and ``ast.Import``
    nodes whose names resolve to it, then deletes those source lines
    (using ``lineno`` / ``end_lineno`` so multi-line parenthesized
    imports are removed whole). All other code is preserved exactly.

    No-op when the code has no matching import.
    """
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        # Unparseable code (e.g. the LLM wrote a stray syntax error)
        # can still contain the development-time import — if it
        # survives into the final script it shadows the vendored
        # inlined helpers at runtime (NotImplementedError). Fall
        # back to a regex strip so the import never leaks through.
        return _strip_imports_regex(python_code)

    kill: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == _TARGET_MODULE:
            kill.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.Import):
            names = [n.name for n in node.names]
            if _TARGET_MODULE in names:
                kill.append((node.lineno, node.end_lineno or node.lineno))

    if not kill:
        return python_code
    return _strip_lines(python_code, kill)


def _strip_lines(python_code: str, kill: list[tuple[int, int]]) -> str:
    """Delete 1-indexed ``(start, end)`` line ranges from ``python_code``."""
    lines = python_code.splitlines(keepends=True)
    kill_ranges = [(start - 1, end - 1) for start, end in kill]
    out = [line for idx, line in enumerate(lines) if not any(lo <= idx <= hi for lo, hi in kill_ranges)]
    return "".join(out)


_IMPORT_FROM_RE = _re.compile(
    r"^[ \t]*from[ \t]+browser_agent\.runtime_helpers[ \t]+import[ \t]*\("
    r"[^)]*\)\s*\n",
    _re.MULTILINE | _re.DOTALL,
)
_IMPORT_FROM_INLINE_RE = _re.compile(
    r"^[ \t]*from[ \t]+browser_agent\.runtime_helpers[ \t]+import[ \t]+"
    r"[^\n]+\n",
    _re.MULTILINE,
)
_IMPORT_BARE_RE = _re.compile(
    r"^[ \t]*import[ \t]+browser_agent\.runtime_helpers[ \t]*\n",
    _re.MULTILINE,
)


def _strip_imports_regex(python_code: str) -> str:
    """Regex fallback: strip ``browser_agent.runtime_helpers`` imports.

    Used when the code does not parse (LLM syntax error). Removes
    parenthesized multi-line imports, single-line ``from ... import``
    lines, and bare ``import`` lines. Over-approximates; the later
    transforms still run and the real syntax error surfaces at run.
    """
    python_code = _IMPORT_FROM_RE.sub("", python_code)
    python_code = _IMPORT_FROM_INLINE_RE.sub("", python_code)
    return _IMPORT_BARE_RE.sub("", python_code)
