"""Shared fixtures and helpers for end-to-end step_0 generation tests.

Starts the fixture HTTP server once per session, provides a helper
that runs the full generation pipeline (``GenerateScriptDriver``)
against a local fixture scenario, and verifies the emitted script's
output against expected criteria.

Each test is a full end-to-end run: fixture server → step_0 driver
(3-agent LLM pipeline) → emitted script subprocess → metadata.db
verification. Tests are slow (minutes each) by design.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from browser_agent.configuration import PROMPTS_PATH, RUNS_FILE, RUNS_PATH

# --- Constants ---

FIXTURE_HOST = "127.0.0.1"
FIXTURE_PORT_BASE = 8765
FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "scripts" / "fixtures"
EMITTED_SCRIPT_TIMEOUT_S = 600.0

# --- Session-scoped fixture server ---


def _find_free_port() -> int:
    """Return the first free port starting from FIXTURE_PORT_BASE."""
    for port in range(FIXTURE_PORT_BASE, FIXTURE_PORT_BASE + 20):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((FIXTURE_HOST, port))
        except OSError:
            continue
        finally:
            sock.close()
        return port
    raise RuntimeError("No free port for fixture server")


@pytest.fixture(scope="session")
def fixture_server():
    """Start the fixture server for the entire test session."""
    server_script = Path(__file__).resolve().parent.parent / "scripts" / "fixture_server.py"
    port = _find_free_port()
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "FIXTURE_PORT": str(port)},
        cwd=str(Path(__file__).resolve().parent.parent / "scripts"),
    )
    time.sleep(1.5)
    # Verify it started
    import urllib.request

    try:
        urllib.request.urlopen(f"http://{FIXTURE_HOST}:{port}/", timeout=3)
    except Exception:
        proc.terminate()
        proc.wait(timeout=5)
        pytest.fail("Fixture server failed to start")
    yield port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# --- Run helpers ---


def run_generation_pipeline(scenario_name: str, prompt: str, fixture_port: int, parallel_runners: int | None = None) -> dict:
    """Run the full step_0 generation pipeline for a scenario.

    Writes the run config YAML, sets active_run.yaml, invokes
    GenerateScriptDriver().run([]), then runs the emitted script
    as a subprocess. Returns a dict with all results.
    """
    run_name = f"e2e_{scenario_name}"
    full_prompt = prompt.replace("{PORT}", str(fixture_port))

    # Write run config
    config: dict[str, object] = {"prompt": full_prompt}
    if parallel_runners is not None:
        config["parallel_runners"] = parallel_runners
    yaml_path = PROMPTS_PATH / f"{run_name}.yaml"
    yaml_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")

    # Set active run
    RUNS_FILE.write_text(yaml.dump({"active_run": f"{run_name}.yaml"}), encoding="utf-8")

    # Clean any previous run dir
    run_path = RUNS_PATH / run_name
    if run_path.exists():
        shutil.rmtree(run_path)

    # Run the generation driver
    from browser_agent.drivers.step_discarted_run_prompt import GenerateScriptDriver

    driver = GenerateScriptDriver()
    try:
        exit_code = driver.run([])
    except Exception:
        exit_code = 2

    # Find and run the emitted scripts: discovery first (populates
    # discovered_links), then processing (reads links, saves records).
    discovery_path, discovery_output = _run_emitted_script(run_path, prefer_discovery=True)
    script_path, smoke_output = _run_emitted_script(run_path, prefer_discovery=False)
    if discovery_output:
        smoke_output = f"[discovery]\n{discovery_output}\n\n[processing]\n{smoke_output}"

    # Verify output
    db_path = run_path / "metadata.db"
    record_count = _record_count(db_path)
    pdf_count = _pdf_count(run_path)

    return {
        "run_name": run_name,
        "run_path": run_path,
        "exit_code": exit_code,
        "script_path": script_path,
        "smoke_output": smoke_output,
        "record_count": record_count,
        "pdf_count": pdf_count,
        "db_path": db_path,
    }


def _run_emitted_script(run_path: Path, prefer_discovery: bool = False) -> tuple[Path | None, str]:
    """Run an emitted .py script under run_path/scripts/.

    When ``prefer_discovery`` is True, pick the discovery script; otherwise
    pick the processing script. Both run against the same ``metadata.db`` so
    the processing script can read links saved by the discovery script.
    """
    scripts_dir = run_path / "scripts"
    if not scripts_dir.is_dir():
        return None, "[no scripts/ directory]"
    candidates = sorted(scripts_dir.glob("*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
    script_path = None
    for p in candidates:
        is_discovery = "discover" in p.name
        if prefer_discovery and is_discovery:
            script_path = p
            break
        if not prefer_discovery and not is_discovery:
            script_path = p
            break
    if script_path is None and candidates:
        script_path = candidates[0]
    if script_path is None:
        return None, "[no emitted script found]"
    env = {
        **os.environ,
        "ZENDRIVER_HEADLESS": "true",
        "BROWSER_AGENT_SAVE_RECORD_DB_PATH": str(run_path / "metadata.db"),
    }
    try:
        import asyncio

        async def _run() -> str:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=EMITTED_SCRIPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"[emitted script timed out after {EMITTED_SCRIPT_TIMEOUT_S}s]"
            return stdout.decode("utf-8", errors="replace") if stdout else ""

        output = asyncio.run(_run())
    except Exception as exc:
        output = f"[emitted script crashed: {exc}]"
    return script_path, output


def _record_count(db_path: Path) -> int:
    """Return row count in metadata table (0 if DB missing)."""
    if not db_path.exists():
        return 0
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM metadata").fetchone()
        conn.close()
        return rows[0] if rows else 0
    except sqlite3.DatabaseError:
        return 0


def _pdf_count(run_path: Path) -> int:
    """Return number of .pdf files under run_path/downloads/."""
    pdf_dir = run_path / "downloads"
    if not pdf_dir.is_dir():
        return 0
    return len(list(pdf_dir.glob("*.pdf")))


def assert_min_records(result: dict, min_records: int) -> None:
    """Assert the run produced at least min_records rows in metadata.db."""
    assert result["record_count"] >= min_records, (
        f"Expected >={min_records} records in metadata.db, got {result['record_count']}. "
        f"Driver exit={result['exit_code']}. Smoke output:\n{result['smoke_output'][-2000:]}"
    )


def assert_pdf_count(result: dict, min_pdfs: int) -> None:
    """Assert the run downloaded at least min_pdfs PDF files."""
    assert result["pdf_count"] >= min_pdfs, (
        f"Expected >={min_pdfs} PDFs in downloads/, got {result['pdf_count']}. "
        f"Smoke output:\n{result['smoke_output'][-2000:]}"
    )


def assert_driver_success(result: dict) -> None:
    """Assert the generation driver exited successfully (exit code 0)."""
    assert result["exit_code"] == 0, (
        f"Generation driver exited with code {result['exit_code']}, expected 0. "
        f"Smoke output:\n{result['smoke_output'][-2000:]}"
    )


def assert_fields_non_null(result: dict, fields: list[str]) -> None:
    """Assert that each field is non-null in at least one row's data JSON."""
    import sqlite3

    db_path = result["db_path"]
    if not db_path.exists():
        pytest.fail(f"metadata.db not found at {db_path} — save_record was never called")
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT data FROM metadata").fetchall()
    conn.close()
    for field in fields:
        found = False
        for (data_json,) in rows:
            try:
                data = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                continue
            val = data.get(field)
            if val is not None and str(val).strip() != "":
                found = True
                break
        assert found, f"Field '{field}' is null/empty in all {len(rows)} rows"


def assert_all_links_processed(result: dict) -> None:
    """Assert no discovered_links row is left status='discovered'.

    The global-gather-timeout bug leaves links unprocessed with no
    metadata row. This check queries the discovered_links table directly.
    """
    import sqlite3

    db_path = result["db_path"]
    if not db_path.exists():
        pytest.fail(f"metadata.db not found at {db_path} — save_record was never called")
    conn = sqlite3.connect(str(db_path))
    try:
        remaining = conn.execute("SELECT COUNT(*) FROM discovered_links WHERE status='discovered'").fetchone()[0]
    except sqlite3.OperationalError:
        remaining = 0
    finally:
        conn.close()
    assert remaining == 0, (
        f"{remaining} link(s) still status='discovered' — the worker pool "
        f"was interrupted before draining all links. Smoke output:\n"
        f"{result['smoke_output'][-2000:]}"
    )


def assert_linter_rules_clean(result: dict, rules: list[str]) -> None:
    """Assert the emitted processing script has no error findings for the given rules."""
    from browser_agent.use_cases.emitted_script_linter import EmittedScriptLinter

    if result["script_path"] is None:
        pytest.fail("No emitted processing script to lint")
    code = result["script_path"].read_text(encoding="utf-8")
    findings = EmittedScriptLinter().lint(code, "processing")
    violations = [f for f in findings if f.rule in rules and f.severity == "error"]
    assert not violations, f"Emitted script violates rules {rules}: " + "; ".join(
        f"rule {f.rule} line {f.line}: {f.message}" for f in violations
    )
