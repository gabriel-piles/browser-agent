from __future__ import annotations

from abc import ABC, abstractmethod

from browser_agent.domain.script_execution_result import ScriptExecutionResult


class ScriptRunnerPort(ABC):
    """Runs a self-contained Python script in a subprocess.

    Implementations launch the project's virtualenv Python on the
    given source string, capture combined stdout/stderr, and return
    a :class:`ScriptExecutionResult`. The agent uses this tool to
    validate its strategy (selectors, scroll loops, filter logic)
    before committing to the final script.
    """

    @abstractmethod
    async def run(self, python_code: str, timeout: float = 120.0) -> ScriptExecutionResult:
        """Execute ``python_code`` and return the captured result.

        Implementations MUST:
        - write the code to a temporary file;
        - run it with the project's ``.venv`` Python (so zendriver
          and any declared dependencies are available);
        - enforce ``timeout`` and treat a timeout as a failure with
          a non-zero exit code;
        - return combined output truncated to a context-safe size.
        """
