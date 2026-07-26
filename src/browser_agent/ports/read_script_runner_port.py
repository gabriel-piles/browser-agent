"""Port for running read-only Python scripts in a subprocess.

Distinct from :class:`ScriptRunnerPort`: the verification agent's
``run_read_script`` tool writes forensic scripts that cross-reference
``metadata.db`` against ``downloads/`` and inspect file integrity — no
browser, no network, no downloads. The runner executes them in an
isolated subprocess so the script has no handle to the agent's
browser session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from browser_agent.domain.script_execution_result import ScriptExecutionResult


class ReadScriptRunnerPort(ABC):
    """Runs a read-only Python script in an isolated subprocess.

    Implementations MUST:
    - write the code to a temporary file;
    - run it with the project's Python (so ``sqlite3`` / ``pathlib`` /
      ``pypdf`` if installed are available);
    - capture combined stdout/stderr;
    - enforce ``timeout`` and treat a timeout as a FAILURE (a forensic
      script that hangs is a bug, not a pass);
    - return combined output truncated to a context-safe size.
    """

    @abstractmethod
    async def run(self, python_code: str, timeout: float = 60.0) -> ScriptExecutionResult:
        """Execute ``python_code`` read-only and return the captured result."""
