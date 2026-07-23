from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from browser_agent.configuration import VALIDATION_PDF_COUNT
from browser_agent.ports.browser_session_port import BrowserSessionPort


@dataclass
class ValidationAgentDeps:
    """Dependency object for the validation agent.

    Separate from :class:`AgentDeps` because the validation agent has
    different tools and dependencies: it carries ``db_path`` and
    ``downloads_path`` for the ``check_pdf`` tool, and a counter/limit
    pair that caps how many PDF checks one agent turn may perform.
    """

    browser_session: BrowserSessionPort
    db_path: Path
    downloads_path: Path
    pdf_checks: int = 0
    pdf_check_limit: int = VALIDATION_PDF_COUNT
