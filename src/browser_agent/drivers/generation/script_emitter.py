"""Apply the post-LLM normalize-launch transform, lint, and write the script to disk.

The emitter applies a single source-level rewrite — ``zd.start(...)`` →
``start_browser(...)`` (``with_emitted_normalize_launch``) — then lints
the raw LLM code, persists it to a ``.raw.py`` sidecar, writes the
transformed source to the path :class:`ScriptPathBuilder` computed, and
writes a sidecar ``.json`` with ``explanation``, ``dependencies``,
``pdf_download_strategy``, the lint findings, and (later) the
smoke-test result. The printed JSON payload drops ``python_code``
(it is already the file).

Helper code is no longer inlined — scripts import from the
``script_tools/`` folder copied beside them by
:class:`ScriptToolsCopier`.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from browser_agent.adapters.emitted_normalize_launch import with_emitted_normalize_launch
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.drivers.generation.script_path_builder import ScriptPathBuilder
from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter


class ScriptEmitter:
    """Normalize-launch transform, lint, write the script + sidecar JSON."""

    def __init__(self, path_builder: ScriptPathBuilder) -> None:
        self._path_builder = path_builder
        self._linter = EmittedScriptLinter()

    def emit(self, task: str, script: GeneratedScript, run_path: Path) -> EmitResult:
        """Lint, persist raw code, transform, write, and return the result."""
        findings = self._linter.lint(script.python_code, kind=script.kind)
        script_path = (
            self._path_builder.build_discovery(task) if script.kind == "discovery" else self._path_builder.build(task)
        )
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
        """Run the normalize-launch transform, logging if it matched."""
        code = script.python_code
        code, applied = self._apply(with_emitted_normalize_launch, code, "normalize_launch")
        return code, applied

    @staticmethod
    def _apply(transform, code: str, name: str) -> tuple[str, list[str]]:
        """Run ``transform`` and record ``name`` if the output changed."""
        out = transform(code)
        return out, [name] if out != code else []

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
    def update_sidecar_prior_feedback(sidecar_path: Path, feedback: str) -> None:
        """Merge the applied prior-run feedback into the existing sidecar JSON."""
        if not sidecar_path.is_file():
            return
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        payload["prior_feedback"] = feedback
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
