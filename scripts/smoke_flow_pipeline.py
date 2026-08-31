"""Smoke-test the flow pipeline end-to-end with stub agents (no LLM, no browser).

Exercises the REAL SplitPipeline: explorer → writer → lint → emit →
smoke subprocess → execute subprocess (against the shared metadata.db)
→ verifier → decision application — with monkeypatched agent classes so
no LLM/browser is needed. Run directly; prints OK at the end.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from typing import cast
from pathlib import Path

sys.path.insert(0, "src")

from browser_agent.domain.flow_verification_report import FlowVerificationReport
from browser_agent.domain.split_run_state import SplitRunState
from browser_agent.domain.verify_decision import VerifyDecision
from browser_agent.drivers.flow.split_pipeline import SplitPipeline
from browser_agent.drivers.flow.split_flow_paths import SplitFlowPaths
from browser_agent.drivers.flow.flow_script_emitter import FlowScriptEmitter
from browser_agent.drivers.flow.flow_script_path_builder import FlowScriptPathBuilder
from browser_agent.use_cases.flow_verifier_use_case import FlowVerifierUseCase
from browser_agent.use_cases.split_state_store import SplitStateStore


GOOD_SCRIPT = """import asyncio
from pathlib import Path
from script_tools.save_record import save_record, load_failed_downloads
from script_tools.start_browser import start_browser

async def main():
    browser = await start_browser(headless=False)
    try:
        out_dir = Path(__file__).resolve().parent.parent.parent / "downloads"
        save_record("https://site/doc1", {"core_file_url": "https://site/doc1.pdf", "core_pdf_filename": "doc1.pdf", "core_download_status": "downloaded"})
        _ = load_failed_downloads()
    finally:
        await browser.stop()

if __name__ == "__main__":
    asyncio.run(main())
"""


class StubExplorer:
    """Returns a canned FlowSubtaskSpec without touching a browser."""

    def __init__(self, deps) -> None:
        self.deps = deps

    async def execute(self, split_prompt: str, context: str = ""):
        from browser_agent.domain.flow_subtask_spec import FlowSubtaskSpec

        assert "PRIOR SPLIT" in context, "explorer must receive the prior split context"
        return FlowSubtaskSpec(subtask_id="sub_stub", description="stub description")

    async def close(self) -> None:
        pass


class StubWriter:
    """Returns the canned good script; ignores prompts."""

    def __init__(self, deps) -> None:
        self.deps = deps

    async def execute_spec(self, spec, context: str = ""):
        from browser_agent.domain.generated_script import GeneratedScript

        assert "sub_stub" in spec.subtask_id
        return GeneratedScript(kind="processing", explanation="stub", python_code=GOOD_SCRIPT)

    async def repair(self, feedback: str):
        from browser_agent.domain.generated_script import GeneratedScript

        return GeneratedScript(kind="processing", explanation="stub", python_code=GOOD_SCRIPT)

    async def close(self) -> None:
        pass


class StubVerifier:
    """Always passes; accepts the gap."""

    def __init__(self, db_path: Path, downloads_path: Path, run_path: Path, verification_dir: Path, **kwargs) -> None:
        self.db_path = db_path
        self.verification_dir = verification_dir
        self.calls: list[list[str]] = []

    async def verify(self, spec, script_sources: list[str]):
        self.calls.append(script_sources)
        report = FlowVerificationReport(
            overall_assessment="complete",
            recommendations="none",
            missing_count=0,
            coverage_complete=True,
            decision=VerifyDecision(action="accept", focus="nothing missing", reasoning="full coverage"),
        )
        from browser_agent.use_cases.verification_report_writer import VerificationReportWriter

        _ = VerificationReportWriter(self.db_path.parent, output_dir=self.verification_dir).write(report)
        return report

    @staticmethod
    def passed(report) -> bool:
        return report.missing_count == 0 and not report.missing_coverage


async def main() -> None:
    import browser_agent.use_cases.flow_explorer_use_case as explorer_mod
    import browser_agent.use_cases.flow_writer_use_case as writer_mod

    explorer_mod.FlowExplorerUseCase = StubExplorer  # type: ignore[misc, assignment]
    writer_mod.FlowWriterUseCase = StubWriter  # type: ignore[misc, assignment]

    with tempfile.TemporaryDirectory() as td:
        run_path = Path(td)
        split_dir = run_path / "flow" / "1_split_one"
        _ = split_dir.mkdir(parents=True)
        (split_dir / "prompt.md").write_text(
            "Original task: get all docs. THIS CHUNK IS IN CHARGE OF: doc1", encoding="utf-8"
        )
        scripts_tools = split_dir / "scripts" / "script_tools"
        scripts_tools.mkdir(parents=True, exist_ok=True)
        (scripts_tools / "start_browser.py").write_text(
            "async def start_browser(**kw):\n    class B:\n        async def stop(self): pass\n    return B()\n",
            encoding="utf-8",
        )
        (scripts_tools / "save_record.py").write_text(
            "import os, sqlite3, json, datetime\n"
            "def _db():\n"
            "    p = os.environ.get('BROWSER_AGENT_SAVE_RECORD_DB_PATH')\n"
            "    assert p, 'executor must set BROWSER_AGENT_SAVE_RECORD_DB_PATH'\n"
            "    return p\n"
            "def save_record(core_id, data):\n"
            "    conn = sqlite3.connect(_db(), timeout=5.0)\n"
            "    conn.execute('CREATE TABLE IF NOT EXISTS metadata (core_id TEXT PRIMARY KEY, task_slug TEXT NOT NULL, scraped_at TEXT NOT NULL, data TEXT NOT NULL)')\n"
            "    conn.execute('INSERT OR REPLACE INTO metadata VALUES (?,?,?,?)', (core_id, os.environ.get('BROWSER_AGENT_TASK_SLUG','s'), datetime.datetime.now(datetime.UTC).isoformat(), json.dumps(data)))\n"
            "    conn.commit(); conn.close()\n"
            "def load_failed_downloads():\n"
            "    return []\n",
            encoding="utf-8",
        )

        paths = SplitFlowPaths(split_dir)
        store = SplitStateStore(paths)
        builder = FlowScriptPathBuilder(paths.scripts_dir())
        emitter = FlowScriptEmitter(builder, create_linter())
        verifier = StubVerifier(
            db_path=run_path / "metadata.db",
            downloads_path=run_path / "downloads",
            run_path=run_path,
            verification_dir=paths.verification_dir(),
        )
        prior = "## PRIOR SPLIT (already finished)\ndescription + script source stub"

        pipeline = SplitPipeline(
            paths=paths,
            state_store=store,
            emitter=emitter,
            verifier=cast(FlowVerifierUseCase, cast(object, verifier)),
            run_path=run_path,
            prior_context=prior,
        )
        state = SplitRunState(split_name=split_dir.name)
        store.save(state)
        state = await pipeline.run(state, (split_dir / "prompt.md").read_text(encoding="utf-8"))

        assert state.finished, state
        assert state.status == "succeeded", state.status
        record = state.scripts[0]
        assert record.status == "succeeded", record
        assert record.script_path and Path(record.script_path).is_file()
        import sqlite3

        conn = sqlite3.connect(run_path / "metadata.db")
        rows = conn.execute("SELECT core_id, task_slug FROM metadata").fetchall()
        conn.close()
        assert rows and rows[0][0] == "https://site/doc1", rows
        assert verifier.calls and "save_record" in verifier.calls[0][0]
        assert (split_dir / "verification" / "verification_report.json").is_file()
        assert (split_dir / "logs" / "script_0_live.log").is_file()
        reloaded = store.load()
        assert reloaded is not None and reloaded.finished and reloaded.status == "succeeded"
        print("PIPELINE SMOKE OK — status:", state.status, "| rows:", rows)


def create_linter():
    from browser_agent.use_cases.flow_script_linter import FlowScriptLinter

    return FlowScriptLinter()


if __name__ == "__main__":
    asyncio.run(main())
