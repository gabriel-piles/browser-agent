from __future__ import annotations

from pydantic import BaseModel, Field


class GeneratedScript(BaseModel):
    """The structured artifact the agent returns for a code-generation run.

    Holds the explanation, the declared pip dependencies and the
    self-contained Python source the caller can persist or execute
    verbatim. All three fields are required so the orchestration layer
    can rely on a single, validated JSON shape.
    """

    explanation: str = Field(
        description=(
            "Step-by-step breakdown of how the generated script solves the "
            "user's workflow. Be specific about selectors, scrolls, waits "
            "and the order in which the page is mutated."
        ),
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description=(
            "Pip packages required to run the generated script. List only "
            "non-stdlib modules the script actually imports. zendriver "
            "and asyncio are stdlib-equivalent for the caller — include "
            "them only if the script needs a specific version."
        ),
    )
    python_code: str = Field(
        description=(
            "A completely self-contained, executable async Python script "
            "utilizing zendriver and asyncio.run(). Must not import or "
            "reference anything from this project; it must be runnable "
            "as a standalone file."
        ),
    )

    def dependency_names(self) -> list[str]:
        """Return the list of pip package names, de-duplicated and stripped."""
        seen: set[str] = set()
        ordered: list[str] = []
        for raw in self.dependencies:
            head = self._strip_spec(raw)
            if head and head.lower() not in seen:
                seen.add(head.lower())
                ordered.append(head)
        return ordered

    @staticmethod
    def _strip_spec(raw: str) -> str:
        """Reduce ``package[extra]>=1.0`` to ``package``."""
        head = raw.strip().split("[", 1)[0]
        for sep in ("==", ">=", "<=", "!=", "~=", ">", "<"):
            if sep in head:
                head = head.split(sep, 1)[0]
                break
        return head.strip()

    def line_count(self) -> int:
        """Return the number of non-blank lines in ``python_code``."""
        return sum(1 for line in self.python_code.splitlines() if line.strip())

    def has_async_main(self) -> bool:
        """True if the script declares an ``async def main`` and calls ``asyncio.run``."""
        src = self.python_code
        return "async def main" in src and "asyncio.run" in src
