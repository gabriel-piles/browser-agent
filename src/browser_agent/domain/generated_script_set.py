from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.generated_script import GeneratedScript


class GeneratedScriptSet(BaseModel):
    """The agent's two-script output: an optional discovery script + a processing script.

    The LLM emits flat fields (``discovery_python_code`` optional,
    ``processing_python_code`` required, plus shared ``explanation``/
    ``dependencies``/``pdf_download_strategy``). The builder methods
    construct :class:`GeneratedScript` objects (with the right ``kind``)
    for the emitter and driver. ``discovery_python_code`` is present only
    when the task needs a separate discovery phase (filter iteration /
    pagination / multi-page link collection); the processing script then
    reads from ``load_discovered_links()``. When absent, the processing
    script does inline extraction (single-page tasks). The concurrency
    directive applies ONLY to the processing script; the discovery
    script is always single-tab.
    """

    discovery_python_code: str | None = None
    processing_python_code: str
    explanation: str = ""
    dependencies: list[str] = Field(default_factory=list)
    pdf_download_strategy: str = "browser_fetch"

    def has_discovery(self) -> bool:
        """True when a non-empty discovery script was emitted."""
        return bool(self.discovery_python_code and self.discovery_python_code.strip())

    def discovery_script(self) -> GeneratedScript | None:
        """Return the discovery script as a ``GeneratedScript`` (kind='discovery')."""
        if not self.has_discovery():
            return None
        assert self.discovery_python_code is not None
        return GeneratedScript(
            kind="discovery",
            explanation=self.explanation,
            dependencies=self.dependencies,
            python_code=self.discovery_python_code,
            pdf_download_strategy=self.pdf_download_strategy,
        )

    def processing_script(self) -> GeneratedScript:
        """Return the processing script as a ``GeneratedScript`` (kind='processing')."""
        return GeneratedScript(
            kind="processing",
            explanation=self.explanation,
            dependencies=self.dependencies,
            python_code=self.processing_python_code,
            pdf_download_strategy=self.pdf_download_strategy,
        )

    def all_scripts(self) -> list[GeneratedScript]:
        """Return non-None scripts in run order (discovery, then processing)."""
        out: list[GeneratedScript] = []
        discovery = self.discovery_script()
        if discovery is not None:
            out.append(discovery)
        out.append(self.processing_script())
        return out

    def dependency_names(self) -> list[str]:
        """Return union of pip package names across all scripts, deduped, order-stable."""
        seen: set[str] = set()
        ordered: list[str] = []
        for script in self.all_scripts():
            for name in script.dependency_names():
                if name.lower() not in seen:
                    seen.add(name.lower())
                    ordered.append(name)
        return ordered
