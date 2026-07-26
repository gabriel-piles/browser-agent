from __future__ import annotations

from pydantic import BaseModel


class LintFinding(BaseModel):
    """A single linter violation found in emitted LLM python_code.

    ``rule`` is the system-prompt rule number (as a string) or
    ``"syntax"`` for a Python parse failure. ``severity`` is either
    ``"error"`` or ``"warning"``. ``line`` is the 1-based line number
    when the check can locate the violation, otherwise ``None``.
    """

    rule: str
    severity: str
    message: str
    line: int | None = None
