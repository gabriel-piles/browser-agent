"""Driver helper that runs the link-discovery-verification agent and emits its script.

Builds a fresh :class:`ZendriverBrowserSession` + :class:`AgentDeps`
(separate from the main generator's session, which is already closed
by the time this runs), runs
:class:`GenerateLinkDiscoveryVerificationUseCase` with the original
task and the main generated script as context, writes the
verification script to ``<run>/scripts/<date>__verify_discovery__<slug>.py``
(the ``script_tools/`` copy is already present from step 0), then
EXECUTES it as a subprocess and judges the verdict. Execution replaces
the old 60-second smoke test: a crash surfaces as a nonzero exit in
the captured output, and the script's own verdict lines (``--- <path>
---`` / ``discovered=`` / ``UNDER-COLLECTED``) drive the judgement.
Failures are logged but never fail step 0 — the verification script is
a secondary artifact.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import signal
import sys
from pathlib import Path

from loguru import logger

from browser_agent.adapters.browser.zendriver_browser_session import (
    ZendriverBrowserSession,
)
from browser_agent.adapters.execution.curl_cffi_pdf_downloader_adapter import (
    CurlCffiPdfDownloaderAdapter,
)
from browser_agent.adapters.execution.in_process_script_runner_adapter import (
    InProcessScriptRunnerAdapter,
)
from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.configuration import ZENDRIVER_HEADLESS
from browser_agent.domain.link_discovery_verdict import LinkDiscoveryVerdict
from browser_agent.domain.link_discovery_verification_script import (
    LinkDiscoveryVerificationScript,
)
from browser_agent.use_cases.agent_deps import AgentDeps
from browser_agent.use_cases.generate_link_discovery_verification_use_case import (
    GenerateLinkDiscoveryVerificationUseCase,
)

_DISCOVERY_SUFFIX = "__verify_discovery"
_TASK_WORD_LIMIT = 8

# Scroll-verifying 70+ links per filter value takes minutes; the 60 s
# smoke budget is useless here (and its timeout-as-PASS semantics would
# kill the script before it prints its verdict).
VERIFICATION_RUN_TIMEOUT_S = 600.0
_REPORT_MAX_CHARS = 8000


class LinkDiscoveryVerificationRunner:
    """Run the link-discovery-verification agent, emit its script, execute + judge it."""

    async def run(self, task: str, original_script_code: str, run_path: Path) -> LinkDiscoveryVerdict:
        """Generate + emit + execute the discovery-verification script (best-effort)."""
        session = self._build_session(run_path)
        deps = self._build_deps(session, run_path)
        use_case = GenerateLinkDiscoveryVerificationUseCase(deps)
        prompt = self._build_prompt(task, original_script_code)
        try:
            script = await use_case.execute(prompt)
        except Exception as exc:
            logger.exception("link-discovery-verification agent failed")
            logger.warning("link-discovery verification UNAVAILABLE — the main script's discovery is UNVERIFIED")
            await use_case.close()
            return LinkDiscoveryVerdict(status="unavailable", report=str(exc))
        path = self._write_script(script, run_path, task)
        await use_case.close()
        verdict = await execute_and_judge_script(path)
        _log_verdict(verdict, path)
        return verdict

    def _build_session(self, run_path: Path) -> ZendriverBrowserSession:
        return ZendriverBrowserSession(
            headless=ZENDRIVER_HEADLESS,
            user_data_dir=run_path / "profile",
        )

    def _build_deps(self, session: ZendriverBrowserSession, run_path: Path) -> AgentDeps:
        return AgentDeps(
            llm=OllamaAdapter(),
            browser_session=session,
            script_runner=InProcessScriptRunnerAdapter(
                browser_session=session,
                metadata_db_path=run_path / "metadata.db",
                task_slug=run_path.name,
            ),
            pdf_downloader=CurlCffiPdfDownloaderAdapter(
                downloads_path=run_path / "downloads",
            ),
        )

    def _build_prompt(self, task: str, original_script_code: str) -> str:
        return (
            f"Original task:\n{task}\n\n"
            "Main scraper script (already generated, for reference — reuse its "
            "filter selectors and navigation strategy, but verify that its LINK "
            "DISCOVERY is complete: (1) replay the main script's OWN discovery "
            "loop read-only and count what IT collects per path "
            "(`main_discovered`); (2) independently re-walk the site with the "
            "full scroll / load-more / dropdown / lazy-load loop as the oracle; "
            "flag every path where `main_discovered` falls short of the "
            "site-advertised total (or of your oracle count when no advertised "
            "total exists)):\n"
            f"```python\n{original_script_code}\n```\n\n"
            "Generate a standalone verification script that confirms the main "
            "scraper discovered ALL PDF links — flagging any filter value where "
            "it under-collected (e.g. stopped at 10 when the site exposes 55)."
        )

    def _write_script(
        self,
        script: LinkDiscoveryVerificationScript,
        run_path: Path,
        task: str,
    ) -> Path:
        scripts_dir = run_path / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.date.today().strftime("%Y_%m_%d")
        path = scripts_dir / f"{today}{_DISCOVERY_SUFFIX}__{_slug(task)}.py"
        path.write_text(script.python_code, encoding="utf-8")
        logger.info("link-discovery-verification script emitted at {path}", path=path)
        return path


async def execute_and_judge_script(script_path: Path, timeout: float = VERIFICATION_RUN_TIMEOUT_S) -> LinkDiscoveryVerdict:
    """Run the verification script as a subprocess and judge its verdict.

    The script runs with the project Python so its sibling
    ``script_tools/`` imports resolve (``sys.path[0]`` is the script's
    own directory — a temp-file copy would break them). Judging: any
    ``UNDER-COLLECTED`` line wins; otherwise exit 0 is ``passed`` and
    a crash/nonzero exit is ``inconclusive`` (no trustworthy verdict).
    """
    cmd = [sys.executable, str(script_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        return LinkDiscoveryVerdict(status="inconclusive", report=f"failed to launch: {exc}")

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_process_group(proc)
        return LinkDiscoveryVerdict(
            status="inconclusive",
            report=f"[verification timed out after {timeout}s]\n[verification script killed — no verdict from the script]",
        )

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    report = _truncate_tail(output, _REPORT_MAX_CHARS)
    if "UNDER-COLLECTED" in output:
        return LinkDiscoveryVerdict(
            status="under_collected",
            report=report,
            under_collected_paths=_under_collected_paths(output),
        )
    if proc.returncode == 0:
        return LinkDiscoveryVerdict(status="passed", report=report)
    return LinkDiscoveryVerdict(status="inconclusive", report=report)


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the process group (Python + Chromium child) on timeout."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def _truncate_tail(output: str, limit: int) -> str:
    """Return ``output`` truncated tail-biased to ``limit`` chars."""
    if len(output) <= limit:
        return output
    keep = limit - 60
    return f"...(head trimmed, total={len(output)} chars)\n{output[-keep:]}"


def _under_collected_paths(output: str) -> list[str]:
    """Extract the paths flagged UNDER-COLLECTED from the script output.

    Matches the print format the verification system prompt mandates:
    ``--- <path> ---`` sets the current path, and any line containing
    ``UNDER-COLLECTED`` attributes the gap to it.
    """
    paths: list[str] = []
    current = "<unknown>"
    for line in output.splitlines():
        header = _path_header(line)
        if header is not None:
            current = header
        elif "UNDER-COLLECTED" in line:
            paths.append(current)
    return paths


def _path_header(line: str) -> str | None:
    """Return the path from a ``--- <path> ---`` header line, or None."""
    stripped = line.strip()
    if not (stripped.startswith("--- ") and stripped.endswith(" ---")):
        return None
    return stripped[4:-4].strip() or None


def _log_verdict(verdict: LinkDiscoveryVerdict, path: Path) -> None:
    """Log the verification verdict prominently for the operator."""
    if verdict.status == "passed":
        logger.info(
            "link-discovery verification PASSED — the main script's discovery is complete ({path})",
            path=path,
        )
        return
    if verdict.status == "under_collected":
        logger.warning(
            "link-discovery verification: main script UNDER-COLLECTS on {paths} ({path})",
            paths=verdict.under_collected_paths,
            path=path,
        )
        return
    if verdict.status == "inconclusive":
        logger.warning(
            "link-discovery verification INCONCLUSIVE — script crashed or timed out ({path}); no repair on ambiguous evidence",
            path=path,
        )
        return
    logger.warning(
        "link-discovery verification UNAVAILABLE — the main script's discovery is UNVERIFIED ({path})",
        path=path,
    )


def _slug(task: str) -> str:
    """Return a filesystem-safe slug derived from the first words of ``task``."""
    words = task.split()[:_TASK_WORD_LIMIT]
    raw = "_".join(words)
    return "".join(c if c.isalnum() else "_" for c in raw.lower()).strip("_") or "generated"
