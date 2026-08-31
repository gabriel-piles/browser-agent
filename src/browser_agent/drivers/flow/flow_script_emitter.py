"""Emit one flow script into its split folder: transform, lint, write, sidecar.

Mirrors the legacy :class:`ScriptEmitter` (normalize-launch transform,
raw sidecar, sidecar JSON) but writes under the split's ``scripts/``
and lints with the flow gate (legacy checks + shared-store rule).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from loguru import logger

from browser_agent.adapters.emitted_normalize_launch import with_emitted_normalize_launch
from browser_agent.adapters.emitted_normalize_extract_fields_module import with_emitted_normalize_extract_fields_module
from browser_agent.domain.emit_result import EmitResult
from browser_agent.domain.generated_script import GeneratedScript
from browser_agent.domain.lint_finding import LintFinding
from browser_agent.drivers.flow.flow_script_path_builder import FlowScriptPathBuilder
from browser_agent.use_cases.flow_script_linter import FlowScriptLinter


class FlowScriptEmitter:
    """Normalize-launch transform, flow lint, write script + sidecar JSON."""

    def __init__(self, path_builder: FlowScriptPathBuilder, linter: FlowScriptLinter) -> None:
        self._path_builder: FlowScriptPathBuilder = path_builder
        self._linter: FlowScriptLinter = linter

    def lint_findings(self, python_code: str) -> list[LintFinding]:
        """Return the error-severity findings for one candidate script."""
        return self._linter.lint(python_code)

    def emit(self, name: str, script: GeneratedScript, script_index: int = 0) -> EmitResult:
        """Lint, persist raw code, transform, write, and return the result."""
        findings = self._linter.lint(script.python_code)
        script_path = self._path_builder.build(name, script_index)
        raw_path = script_path.with_suffix(".raw.py")
        raw_path.write_text(script.python_code, encoding="utf-8")
        final_code, applied = self._finalize_source(script)
        _ = script_path.write_text(final_code, encoding="utf-8")
        sidecar_path = self._write_sidecar(script, script_path, findings)
        self._log_transforms(applied)
        return EmitResult(
            script_path=script_path,
            sidecar_path=sidecar_path,
            raw_code_path=raw_path,
            lint_findings=findings,
            transforms_applied=applied,
        )

    def _finalize_source(self, script: GeneratedScript) -> tuple[str, list[str]]:
        """Run the normalize-launch transform, logging if it matched."""
        code = script.python_code
        code, applied = self._apply(with_emitted_normalize_launch, code, "normalize_launch")
        code, a2 = self._apply(with_emitted_normalize_extract_fields_module, code, "normalize_extract_fields_module")
        applied += a2
        return code, applied

    @staticmethod
    def _apply(transform: Callable[[str], str], code: str, name: str) -> tuple[str, list[str]]:
        """Run ``transform`` and record ``name`` if the output changed."""
        out = transform(code)
        return out, [name] if out != code else []

    def _write_sidecar(
        self,
        script: GeneratedScript,
        script_path: Path,
        findings: list[LintFinding],
    ) -> Path:
        """Write a sidecar JSON with explanation, strategy, lint findings."""
        sidecar = script_path.with_suffix(".json")
        payload = {
            "script_path": script_path.name,
            "raw_code_path": script_path.with_suffix(".raw.py").name,
            "kind": script.kind,
            "explanation": script.explanation,
            "dependencies": script.dependency_names(),
            "pdf_download_strategy": script.pdf_download_strategy,
            "lint_findings": [f.model_dump() for f in findings],
        }
        _ = sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return sidecar

    @staticmethod
    def _log_transforms(applied: list[str]) -> None:
        """Log which transforms actually matched (vs silently no-op'd)."""
        if applied:
            logger.info("emit transforms applied: {names}", names=", ".join(applied))
        else:
            logger.info("emit transforms: none matched (code passed through unchanged)")
