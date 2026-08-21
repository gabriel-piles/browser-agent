"""Rewrite ``from script_tools.extract_rows import X`` / ``from script_tools.extract_links import X`` → ``from script_tools.extract_fields import X`` in emitted scripts.

``extract_rows`` and ``extract_links`` are functions in ``extract_fields.py``,
not modules; the LLM sometimes halluculates the module name. This normalizer
rewrites the module path only, leaving the imported names untouched.
"""

from __future__ import annotations

import re

_EXTRACT_FIELDS_MODULE_RE = re.compile(r"from\s+script_tools\.(?:extract_rows|extract_links)\s+import\s+")


def with_emitted_normalize_extract_fields_module(python_code: str) -> str:
    """Rewrite ``from script_tools.extract_rows/extract_links import X`` → ``from script_tools.extract_fields import X``."""
    rewritten = 0

    def _replace(_match: "re.Match[str]") -> str:
        nonlocal rewritten
        rewritten += 1
        return "from script_tools.extract_fields import "

    normalized = _EXTRACT_FIELDS_MODULE_RE.sub(_replace, python_code)
    if rewritten:
        from loguru import logger

        logger.info("emitted-script normalizer rewrote extract_rows/extract_links module import → extract_fields")
    return normalized
