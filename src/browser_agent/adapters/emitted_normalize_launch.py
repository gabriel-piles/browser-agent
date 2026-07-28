"""Rewrite ``zd.start(...)`` calls in the LLM's emitted code to ``start_browser(...)``.

The only source-level transform that survives the move to importable
``script_tools``. The LLM sometimes emits ``await zd.start(headless=False)``
instead of ``await start_browser(headless=False)``; this normalizer rewrites
every match, dropping arguments the agent does not expose (``user_agent``)
and passing through the rest unchanged. Runs on the LLM's code BEFORE any
helpers are imported, so it never touches the helper modules themselves.
"""

from __future__ import annotations

import re

# Match a call to zendriver's start() that the LLM emitted. Captures
# any preceding ``await`` / argument list (including multiline). The
# first alternative covers ``import zendriver as zd; zd.start(...)``,
# the second covers ``import zendriver; zendriver.start(...)`` and
# any other alias. Whitespace before the call is preserved.
_EMITTED_ZD_START_RE = re.compile(
    r"(?P<head>(?:await\s+)?)(?P<callee>\bzd\.start\b|\bzendriver\.start\b)"
    r"(?P<args>\s*\([^()]*?(?:\([^()]*\)[^()]*)*\))",
    re.DOTALL,
)


def with_emitted_normalize_launch(python_code: str) -> str:
    """Rewrite ``zd.start(...)`` calls in the LLM's emitted code.

    The ``script_tools.start_browser`` function replaces ``zd.start()``:
    it launches Chromium with minimal flags, seeds the profile, injects
    stealth JS, and patches ``browser.stop()``. The LLM sometimes emits
    ``await zd.start(headless=False)`` instead, which passes ~22
    automation-flagging Chrome arguments and triggers anti-bot checks.
    The in-process validation runner shims ``zendriver.start`` to share
    the agent's tab, so validation succeeds even with ``zd.start()`` —
    the bug only surfaces when the operator runs the final script.

    This normalizer runs on the LLM's code before any helpers are
    imported, so it never touches the helper modules. It rewrites every
    match to a call to ``start_browser()``, dropping arguments the agent
    does not expose (``user_agent``) and passing through the rest
    unchanged.
    """
    rewritten = 0

    def _replace(match: "re.Match[str]") -> str:
        nonlocal rewritten
        rewritten += 1
        head = match.group("head")
        args_text = match.group("args")
        cleaned = _strip_user_agent_kwarg(args_text)
        return f"{head}start_browser{cleaned}"

    normalized = _EMITTED_ZD_START_RE.sub(_replace, python_code)
    if rewritten:
        from loguru import logger

        logger.info(
            "emitted-script normalizer rewrote {n} zd.start() call(s) to start_browser()",
            n=rewritten,
        )
    return normalized


def _strip_user_agent_kwarg(args_text: str) -> str:
    """Remove ``user_agent=...`` from an argument list, comma-safe.

    Preserves any whitespace inside the parens. If the cleaned
    argument list becomes empty (or just whitespace), returns the
    original text with parens preserved (so ``zd.start()`` becomes
    ``start_browser()``).
    """
    pattern = re.compile(
        r",?\s*user_agent\s*=\s*"
        r"(?:'[^'\\]*(?:\\.[^'\\]*)*'"
        r"|\"[^\"\\]*(?:\\.[^\"\\]*)*\""
        r"|\([^)]*\)"
        r"|\[[^\]]*\]"
        r"|\{[^{}]*\}"
        r"|[^,()[\]{}])",
        re.DOTALL,
    )
    cleaned = pattern.sub("", args_text)
    cleaned = re.sub(r"\(\s*,", "(", cleaned)
    return cleaned or "()"
