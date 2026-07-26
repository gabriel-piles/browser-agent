"""Apply the post-LLM transforms, lint, and write the executable script to disk.

Hides the chain of source-level rewrites the agent's emitted
code needs before it can run as a standalone script:

0. ``with_emitted_strip_imports`` removes the
   ``from browser_agent.runtime_helpers import ...`` line the LLM
   writes so it can see typed signatures during generation. The
   import is a development-time anchor; the final script is
   self-contained.
1. ``with_emitted_normalize_launch`` rewrites ``zd.start(...)`` to
   ``start_browser(...)`` so the script does not pass automation-
   flagging Chrome args that trigger anti-bot checks.
2. ``with_emitted_inject_profile_path`` points the emitted script
   at the agent's warm profile directory.
3. The remaining transforms prepend the vendored helper
   definitions the script depends on.

Before writing, the raw LLM ``python_code`` is persisted to a
``.raw.py`` sidecar so a crash in the transform chain never loses
the generation. The transformed source is then linted (rule check),
written to the path :class:`ScriptPathBuilder` computed, and a
sidecar ``.json`` is written with ``explanation``, ``dependencies``,
``pdf_download_strategy``, the lint findings, and (later) the
smoke-test result. The printed JSON payload drops ``python_code``
(it is already the file).
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from browser_agent.adapters.emitted_clean_launch import (
    with_emitted_clean_launch,
    with_emitted_inject_profile_path,
    with_emitted_normalize_launch,
)
from browser_agent.adapters.emitted_page_wait import with_emitted_page_wait
from browser_agent.adapters.emitted_pdf_download import with_emitted_pdf_download
from browser_agent.adapters.emitted_save_html import with_emitted_save_html
from browser_agent.adapters.emitted_save_record import with_emitted_save_record
from browser_agent.adapters.emitted_strip_imports import (
    with_emitted_strip_imports,
)
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter


class ScriptEmitter:
    """Apply post-LLM transforms, lint, write the script + sidecar JSON."""

    def __init__(self, path_builder: ScriptPathBuilder) -> None:
        self._path_builder = path_builder
        self._linter = EmittedScriptLinter()

    def emit(self, task: str, script: GeneratedScript, run_path: Path) -> EmitResult:
        """Lint, persist raw code, transform, write, and return the result."""
        findings = self._linter.lint(script.python_code)
        script_path = self._path_builder.build(task)
        raw_path = self._raw_path(script_path)
        raw_path.write_text(script.python_code, encoding="utf-8")
        final_code, applied = self._finalize_source(script, run_path)
        script_path.write_text(final_code, encoding="utf-8")
        sidecar_path = self._write_sidecar(script, script_path, run_path, findings)
        self._log_transforms(applied)
        self._print_payload(script, script_path, run_path)
        return EmitResult(
            script_path=script_path,
            sidecar_path=sidecar_path,
            raw_code_path=raw_path,
            lint_findings=findings,
            transforms_applied=applied,
        )

    def _finalize_source(self, script: GeneratedScript, run_path: Path) -> tuple[str, list[str]]:
        """Run every source-level transform, logging which ones matched."""
        code = script.python_code
        applied: list[str] = []
        code, n = self._apply(with_emitted_strip_imports, code, "strip_imports")
        applied.extend(n)
        code, n = self._apply(with_emitted_normalize_launch, code, "normalize_launch")
        applied.extend(n)
        code = with_emitted_inject_profile_path(code, self._profile_path(run_path))
        code, n = self._apply(with_emitted_clean_launch, code, "clean_launch")
        applied.extend(n)
        code, n = self._apply(with_emitted_page_wait, code, "page_wait")
        applied.extend(n)
        code, n = self._apply(with_emitted_save_record, code, "save_record")
        applied.extend(n)
        code, n = self._apply(with_emitted_save_html, code, "save_html")
        applied.extend(n)
        code, n = self._apply(
            lambda c: with_emitted_pdf_download(c, script.pdf_download_strategy),
            code,
            "pdf_download",
        )
        applied.extend(n)
        return code, applied

    @staticmethod
    def _apply(transform, code: str, name: str) -> tuple[str, list[str]]:
        """Run ``transform`` and record ``name`` if the output changed."""
        out = transform(code)
        return out, [name] if out != code else []

    def _profile_path(self, run_path: Path) -> str:
        """Return the absolute profile path the emitted script must reuse."""
        return str((run_path / "profile").resolve())

    @staticmethod
    def _raw_path(script_path: Path) -> Path:
        """Return the ``.raw.py`` sidecar path for the raw LLM code."""
        return script_path.with_suffix(".raw.py")

    def _write_sidecar(
        self,
        script: GeneratedScript,
        script_path: Path,
        run_path: Path,
        findings: list[LintFinding],
    ) -> Path:
        """Write a sidecar JSON with explanation, strategy, lint findings."""
        sidecar = script_path.with_suffix(".json")
        payload = {
            "script_path": str(script_path),
            "raw_code_path": str(self._raw_path(script_path)),
            "metadata_db_path": str(run_path / "metadata.db"),
            "explanation": script.explanation,
            "dependencies": script.dependency_names(),
            "pdf_download_strategy": script.pdf_download_strategy,
            "lint_findings": [f.model_dump() for f in findings],
        }
        sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return sidecar

    @staticmethod
    def update_sidecar_smoke(sidecar_path: Path, smoke_result: dict[str, object]) -> None:
        """Merge the smoke-test result into the existing sidecar JSON."""
        if not sidecar_path.is_file():
            return
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        payload["smoke_test"] = smoke_result
        sidecar_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _log_transforms(applied: list[str]) -> None:
        """Log which transforms actually matched (vs silently no-op'd)."""
        if applied:
            logger.info("emit transforms applied: {names}", names=", ".join(applied))
        else:
            logger.info("emit transforms: none matched (code passed through unchanged)")

    def _print_payload(self, script: GeneratedScript, script_path: Path, run_path: Path) -> None:
        """Print the structured payload as JSON, WITHOUT ``python_code``."""
        payload = script.model_dump(exclude={"python_code"})
        payload["script_path"] = str(script_path)
        payload["metadata_db_path"] = str(run_path / "metadata.db")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
