"""Render the deterministic reconciler inventory to markdown + JSON.

The reconciler runs *before* the agent and its output is written to
disk so a model failure mid-run still leaves usable evidence. The
markdown section is also handed to the agent as ground truth so it does
not re-derive (and mis-transcribe) the per-PDF table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from browser_agent.domain.corpus_finding import CorpusFinding
from browser_agent.domain.reconciled_pdf import ReconciledPdf

_RECONCILER_MD = "reconciler_inventory.md"
_RECONCILER_JSON = "reconciler_inventory.json"


class ReconcilerReportWriter:
    """Persist the reconciler inventory as markdown and JSON."""

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path

    def write(self, per_row: list[ReconciledPdf], findings: list[CorpusFinding]) -> tuple[Path, Path]:
        """Write both artifacts and return ``(md_path, json_path)``."""
        md = self._render(per_row, findings)
        payload = self._payload(per_row, findings)
        md_path = self._run_path / _RECONCILER_MD
        json_path = self._run_path / _RECONCILER_JSON
        _ = md_path.write_text(md, encoding="utf-8")
        _ = json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return md_path, json_path

    def render_section(self, per_row: list[ReconciledPdf], findings: list[CorpusFinding]) -> str:
        """Return the markdown section for embedding in the agent prompt."""
        return self._render(per_row, findings)

    def _render(self, per_row: list[ReconciledPdf], findings: list[CorpusFinding]) -> str:
        lines = [
            "## Deterministic Reconciler Inventory (DB vs disk, no LLM)",
            "",
            self._summary(per_row),
            "",
            self._table(per_row),
            "",
            self._findings_section(findings),
        ]
        return "\n".join(lines)

    def _summary(self, per_row: list[ReconciledPdf]) -> str:
        total = len(per_row)
        present = sum(1 for r in per_row if r.verdict == "present")
        missing = sum(1 for r in per_row if r.verdict == "file_not_downloaded")
        corrupt = sum(1 for r in per_row if r.verdict == "corrupt_file")
        small = sum(1 for r in per_row if r.verdict == "suspiciously_small")
        empty = sum(1 for r in per_row if r.verdict == "empty_pdf_url")
        mismatch = sum(1 for r in per_row if r.filename_mismatch)
        return (
            f"- DB rows: {total}\n"
            f"- Present: {present}\n"
            f"- File not downloaded: {missing}\n"
            f"- Corrupt: {corrupt}\n"
            f"- Suspiciously small: {small}\n"
            f"- Empty pdf_url: {empty}\n"
            f"- Filename mismatch (step-0 bug): {mismatch}"
        )

    def _table(self, per_row: list[ReconciledPdf]) -> str:
        header = (
            "## Per-row inventory\n\n"
            "| pdf_url | verdict | match_mode | file | size | dl_status | notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- |"
        )
        rows = [self._table_row(r) for r in per_row]
        return header + ("\n" + "\n".join(rows) if rows else "")

    def _table_row(self, r: ReconciledPdf) -> str:
        url = _short(r.pdf_url or r.source_url)
        dl_status = r.download_status or "-"
        return (
            f"| {url} | {r.verdict} | {r.match_mode} | "
            f"{r.matched_filename or '-'} | {r.file_size_bytes} | {dl_status} | {_inline(r.notes)} |"
        )

    def _findings_section(self, findings: list[CorpusFinding]) -> str:
        if not findings:
            return "## Corpus findings\n\nNo whole-corpus anomalies detected."
        blocks = [self._finding_block(f) for f in findings]
        return "## Corpus findings\n\n" + "\n\n".join(blocks)

    def _finding_block(self, f: CorpusFinding) -> str:
        items = "\n".join(f"  - {i}" for i in f.items) if f.items else "  (none listed)"
        return f"### {f.kind}\n\n{f.detail}\n\n{items}"

    def _payload(self, per_row: list[ReconciledPdf], findings: list[CorpusFinding]) -> dict[str, Any]:
        return {
            "per_row": [r.model_dump() for r in per_row],
            "findings": [f.model_dump() for f in findings],
        }


def _short(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _inline(text: str) -> str:
    return text.replace("|", "\\|") if text else ""
