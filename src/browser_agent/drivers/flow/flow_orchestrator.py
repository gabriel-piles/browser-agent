"""Deterministic orchestrator for the new step-1 flow.

Sequences the selected split folders one at a time — no next split
starts before the previous finished — and wires each split's pipeline
with the shared run-root ``downloads/`` and ``metadata.db``. All
decisions after verification come from the verify agent's structured
``decision``; there is no LLM orchestrator.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from browser_agent.domain.split_run_state import SplitRunState
from browser_agent.drivers.flow.flow_script_emitter import FlowScriptEmitter
from browser_agent.drivers.flow.flow_script_path_builder import FlowScriptPathBuilder
from browser_agent.drivers.flow.split_flow_paths import SplitFlowPaths
from browser_agent.drivers.flow.split_pipeline import SplitPipeline, _now
from browser_agent.use_cases.flow_script_linter import FlowScriptLinter
from browser_agent.use_cases.flow_verifier_use_case import FlowVerifierUseCase
from browser_agent.use_cases.split_state_store import SplitStateStore


class FlowOrchestrator:
    """Run the selected splits sequentially, each to a terminal outcome."""

    def __init__(
        self,
        run_path: Path,
        require_html_files: bool,
        original_task: str,
    ) -> None:
        self._run_path = run_path
        self._require_html_files = require_html_files
        self._original_task = original_task
        self._prior_block = ""

    async def run(self, split_dirs: list[Path]) -> int:
        """Run every selected split in order; return the process exit code."""
        code = 0
        for split_dir in split_dirs:
            outcome = await self._run_split(split_dir)
            if outcome != 0:
                code = outcome
        return code

    async def _run_split(self, split_dir: Path) -> int:
        """Drive one split folder end-to-end; 0 on success, 1 on terminal failure."""
        from browser_agent.adapters.browser.clean_browser_launcher import kill_chromium_under
        from browser_agent.drivers.generation.script_tools_copier import ScriptToolsCopier
        from browser_agent.llm_transcript_logger import configure_llm_transcript_dir
        from browser_agent.logging_config import add_run_log_file

        name = split_dir.name
        logger.info("flow: starting split {name}", name=name)
        kill_chromium_under(self._run_path)
        # The split's script runs as a subprocess with sys.path[0] = its own
        # scripts/ dir, so script_tools must live beside it (self-contained).
        ScriptToolsCopier().copy(split_dir)

        paths = SplitFlowPaths(split_dir)
        state_store = SplitStateStore(paths)
        split_prompt = self._read_prompt(split_dir)
        prior_context = self._prior_block
        pipeline = SplitPipeline(
            paths=paths,
            state_store=state_store,
            emitter=self._emitter(paths),
            verifier=self._verifier(paths, split_prompt),
            run_path=self._run_path,
            prior_context=prior_context,
            original_task=self._original_task,
        )
        state = state_store.load() or SplitRunState(split_name=name)
        state.started_at = state.started_at or _now()
        state_store.save(state)
        # Keep each split self-contained: its own run.log sink + LLM transcripts.
        split_sink = add_run_log_file(paths.logs_dir() / "run.log")
        try:
            configure_llm_transcript_dir(paths.debug_dir() / "llm")
            state = await pipeline.run(state, split_prompt)
        except Exception:
            logger.exception("split {name} crashed", name=name)
            state.status = "crashed"
            state.finished = True
            state_store.save(state)
            return 2
        finally:
            logger.remove(split_sink)
            configure_llm_transcript_dir(self._run_path / "debug" / "llm")
        self._prior_block = self._render_prior_block(state, paths)
        if state.status == "succeeded":
            logger.info("flow: split {name} succeeded", name=name)
            return 0
        logger.warning("flow: split {name} finished with status={status}", name=name, status=state.status)
        return 1

    def _read_prompt(self, split_dir: Path) -> str:
        """Read the split's prompt.md (step 0 wrote it)."""
        path = split_dir / "prompt.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _emitter(self, paths: SplitFlowPaths) -> FlowScriptEmitter:
        """Build the split's emitter with its flow linter."""
        builder = FlowScriptPathBuilder(paths.scripts_dir())
        linter = FlowScriptLinter(require_html_files=self._require_html_files)
        return FlowScriptEmitter(builder, linter)

    def _verifier(self, paths: SplitFlowPaths, split_prompt: str) -> FlowVerifierUseCase:
        """Build the split's verifier against the SHARED run-root stores."""
        return FlowVerifierUseCase(
            db_path=self._run_path / "metadata.db",
            downloads_path=self._run_path / "downloads",
            run_path=self._run_path,
            verification_dir=paths.verification_dir(),
            require_html_files=self._require_html_files,
            original_task=self._original_task,
            split_prompt=split_prompt,
        )

    def _render_prior_block(self, state: SplitRunState, paths: SplitFlowPaths) -> str:
        """Render the finished split's spec+script as the NEXT split's prior context."""
        primary = next((r for r in state.scripts if r.script_index == 0 and r.script_path), None)
        if primary is None or not state.spec:
            return ""
        path = Path(primary.script_path)
        if not path.is_file():
            return ""
        return (
            "## PRIOR SPLIT (already finished — its script is a proven starting point)\n"
            f"### Its subtask description:\n{state.spec.get('description', '')}\n\n"
            f"### Its emitted script ({path.name}):\n```python\n{path.read_text(encoding='utf-8', errors='replace')}\n```\n"
            f"Adapt this script for YOUR split when its mechanics fit — change only what your scope requires."
        )
