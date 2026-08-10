# Robustness Test–Fix Loop for Script Generation

## Context

The driver `step_0_generate_script.py` orchestrates a 3-agent LLM pipeline (Explorer → Discovery Writer → Processing Writer) that generates self-contained Python scraping scripts using `script_tools/*` helpers. The generated scripts must work reliably on any target site, but there is no test suite, and robustness gaps are discovered only during real runs against specific sites (Corte IDH, IACHR, etc.). The user wants an autonomous loop: an LLM agent designs test scenarios backed by a local web server, runs the generation pipeline against them, diagnoses failures, patches the root cause in the codebase (prompts, `script_tools`, linter rules, error patterns), and re-runs — progressively harder scenarios until the agent reliably produces working scripts for any site.

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │            Robustness Loop Driver                 │
                    │  (scripts/robustness_loop.py)                     │
                    │                                                   │
                    │  for scenario in scenario_queue:                  │
                    │    1. Start local fixture server for scenario     │
                    │    2. Create run config (YAML prompt) for scenario│
                    │    3. Run step_0_generate_script driver           │
                    │    4. Run emitted script → verify expected output │
                    │    5. On failure: diagnose → patch → re-run       │
                    │    6. On success: generate harder scenario        │
                    │                                                   │
                    │  Loop until N consecutive passes or max iterations │
                    └──────────┬──────────────┬──────────────┬─────────┘
                               │              │              │
                    ┌──────────▼──┐ ┌─────────▼──┐ ┌────────▼────────┐
                    │ Fixture     │ │ Driver      │ │ Fix Agent        │
                    │ Server      │ │ Runner      │ │ (LLM subagent)   │
                    │ (stdlib     │ │             │ │                  │
                    │ http.server)│ │ invokes     │ │ reads failure    │
                    │ serves HTML │ │ step_0      │ │ output + code    │
                    │ fixtures    │ │ driver      │ │ patches root     │
                    │ per scenario│ │             │ │                  │
                    └─────────────┘ └─────────────┘ └──────────────────┘
```

## Key Design Decisions
1. **Local web server, not real sites.** The loop needs deterministic, reproducible failures. A local `http.server`-based server serves LLM-generated HTML fixtures that simulate real site patterns. No network dependency, no rate limits, no site drift. Each scenario is a fixture directory served at `http://localhost:<port>/`.

2. **Fully autonomous fix agent.** An LLM subagent reads the failure output (smoke test stderr, lint findings, wrong-data diff), reads the relevant code files, produces a diagnosis, and directly edits the codebase (prompts, `script_tools`, linter rules, error patterns). No human approval gate. A git checkpoint before each patch allows rollback if the fix makes things worse.

3. **Scenario difficulty escalates.** The loop starts with trivial scenarios (single-page extraction) and escalates to complex ones (SPA with infinite scroll, dropdown filters, PDF download modal, concurrency). The scenario generator (an LLM call) reads the codebase's current failure modes and designs the next scenario to probe a gap not yet covered.

4. **The loop targets the generation pipeline, not just the output.** When a generated script fails, the fix is at the root: the system prompts (explorer, discovery writer, processing writer), the `script_tools/*` helpers, the `EmittedScriptLinter` rules, or the `ZD_RUNTIME_ERROR_PATTERNS`. Patching a single generated script would not improve robustness — the fix must make the *agent* produce better scripts for all future sites.

5. **AGENTS.md compliance.** No `__init__.py` files, no `__all__` variables, constants in scripts (not arguments), pydantic models in `domain/` (one file per class), methods ≤16 lines, ≤11 methods per class, no tests unless explicitly asked (the user explicitly asked here).

## Approach

### Step 1 — Create the fixture server module

**File:** `scripts/fixture_server.py`

A lightweight HTTP server (stdlib `http.server`) that serves HTML fixtures from a directory. Each scenario is a directory under `scripts/fixtures/<scenario_name>/` containing:

- `server.py` or static HTML files — the fixture pages
- `manifest.json` — scenario metadata: URL paths, expected output shape, difficulty level, what pattern it tests

The server is started as a subprocess on a configurable port (constant in the script, not an argument per AGENTS.md). It serves:

- Static HTML pages for simple scenarios
- Dynamic routes for SPA/scroll/filter scenarios (server-side renders pages based on query params like `?page=N`, `?filter=value`)
- PDF file routes (serves real small PDF bytes from a fixture file)
- Simulated download modals (links that point to PDF routes)

**Reuse:** No existing web server in the repo. **Decision: use `http.server.BaseHTTPRequestHandler`** — no new dependency, sufficient for serving static + dynamic HTML, and keeps the fixture server self-contained. The handler subclasses `BaseHTTPRequestHandler` and routes by path/query, reading fixture HTML from the scenario directory.

**Constants:**
```python
FIXTURE_HOST = "127.0.0.1"
FIXTURE_PORT = 8765
FIXTURES_ROOT = Path(__file__).parent / "fixtures"
```

**Edge cases:** Port already in use → try next port (scan a small range). Fixture directory missing → return 404. PDF route → serve bytes with `Content-Type: application/pdf`.

### Step 2 — Create the scenario model and scenario runner

**File:** `src/browser_agent/domain/robustness_scenario.py`

Pydantic model:
```python
class RobustnessScenario(BaseModel):
    name: str          # scenario slug, e.g. "single_page_list"
    difficulty: int    # 1-5, controls escalation
    pattern: str       # what site pattern it tests, e.g. "infinite_scroll"
    prompt: str        # the natural-language scraping task (becomes the run YAML prompt)
    fixture_dir: str   # relative path to the fixture directory
    expected: ExpectedOutput  # what the emitted script should produce
    description: str    # human-readable explanation of what this scenario probes
```

**File:** `src/browser_agent/domain/expected_output.py`

Pydantic model:
```python
class ExpectedOutput(BaseModel):
    min_records: int           # at least N save_record calls (rows in metadata.db)
    required_fields: list[str]  # fields that must be non-null in at least one row
    pdf_count: int              # expected number of PDF files downloaded
    description: str            # what correct output looks like
```

**File:** `src/browser_agent/domain/scenario_result.py`

Pydantic model:
```python
class ScenarioResult(BaseModel):
    scenario_name: str
    success: bool
    failures: list[str]         # empty when success=True
    emitted_script_path: str | None  # path to the generated .py
    smoke_output: str           # stdout+stderr from the emitted script run
    driver_exit_code: int       # step_0 driver exit code (0=ok, 1=smoke fail, 2=run fail)
    metadata_db_path: str | None  # path to metadata.db if it exists
    pdf_count: int              # PDFs found in downloads/
    record_count: int           # rows in metadata table
```

### Step 3 — Create the scenario fixture generator

**File:** `scripts/generate_scenario.py`

A script that uses the project's `OllamaAdapter` to obtain a pydantic-ai `Model`, then builds a `pydantic_ai.Agent(output_type=dict)` for one-shot scenario generation. It:

1. Reads the current failure history (`scripts/failures_log.jsonl`) to understand what patterns are failing
2. Reads the system prompts and `script_tools` to understand what the agent currently supports
3. Generates: a scenario description, HTML fixtures, a manifest, and a prompt that would exercise the generation pipeline
4. Writes everything to `scripts/fixtures/<scenario_name>/`

The LLM call uses the project's `OllamaAdapter.get_model()` + `pydantic_ai.Agent(output_type=dict)` — a one-shot structured-JSON call with a system prompt that explains the codebase's architecture and asks for a scenario + fixtures that would probe a specific gap. The agent returns structured JSON: `{name, difficulty, pattern, prompt, fixture_files: {path: content}, expected: {min_records, required_fields, pdf_count}}`. The runner writes the fixture files to disk.

**Scenario patterns to generate, in escalating difficulty:**

| Level | Pattern | What it probes |
|-------|---------|-----------------|
| 1 | Single page, static list of items | Basic extraction, save_record, selectors |
| 2 | Multi-page pagination (Next button) | Pagination loop, link collection |
| 3 | Dropdown filter with multiple options | `select_filter_value`, filter iteration, dedup |
| 4 | Infinite scroll / lazy loading | `discover_links` with scroll_js, load-more |
| 5 | SPA with dynamic rendering (JS-rendered content) | `wait_for_page_ready`, `wait_for_anchors` |
| 6 | PDF download from detail page (modal/button) | `download_pdf_*`, PDF classification |
| 7 | Mixed content types (PDFs + HTML docs) | `file_ext_for`, document type classification |
| 8 | Concurrency (parallel_runners=N) | asyncio.Queue, gate_lock, per-tab workers |

**Constants:**
```python
MAX_SCENARIOS = 50
MAX_FIX_ATTEMPTS_PER_SCENARIO = 3
MAX_LOOP_ITERATIONS = 100
CONSECUTIVE_PASSES_TO_STOP = 5
```

### Step 4 — Create the driver runner

**File:** `scripts/run_scenario.py`

Orchestrates a single scenario end-to-end:

1. Start the fixture server for the scenario
2. Create a run config YAML in `data/prompts/robustness_<scenario>.yaml` with the scenario's prompt (pointing at `http://127.0.0.1:8765/...`)
3. Set `data/active_run.yaml` to point at this run
4. Invoke `GenerateScriptDriver().run([])` — the existing driver
5. After the driver completes, run the emitted script from `data/runs/robustness_<scenario>/scripts/*.py`
6. Verify the output against `ExpectedOutput`:
   - Query `metadata.db` for row count ≥ `min_records`
   - Check required fields are non-null
   - Count PDF files in `downloads/` directory
7. Return a `ScenarioResult` (pass/fail + failure output)

**Reuse:** `GenerateScriptDriver` from `step_0_generate_script.py` — called directly, not as a subprocess. `RunsConfigLoader` for config management. `smoke_test_script` from `script_smoke_tester.py` for running the final emitted script with a real timeout.

**Key detail:** The driver expects `data/active_run.yaml` to name the active run. The runner writes this YAML before each scenario and cleans up after. The run directory under `data/runs/` is created by `RunsConfigLoader._run_path()`.

### Step 5 — Create the diagnosis + fix agent

**File:** `scripts/fix_agent.py`

The core of the autonomous fix loop. Given a `ScenarioResult` with a failure, it:

1. **Collects evidence:** failure output (smoke test stderr, lint findings, wrong-data diff), the emitted script, the scenario prompt, and the expected output.

2. **Diagnoses root cause:** An LLM call using the project's `OllamaAdapter.get_model()` + `pydantic_ai.Agent(output_type=dict)` with a carefully crafted prompt that:
   - Reads the emitted script that failed
   - Reads the relevant system prompt (explorer/discovery/processing writer)
   - Reads the relevant `script_tools/*` module
   - Reads the linter rules in `emitted_script_linter.py`
   - Reads `zendriver_error_patterns.py`
   - Produces a structured diagnosis: `{root_cause, affected_files, proposed_patch, reasoning}`

3. **Applies the patch:** The fix agent returns `{diagnosis, files_to_edit: [{path, old_content, new_content}]}`. The runner applies these edits via the `edit` tool (for surgical changes) or `write` tool (for full-file replacements). For complex structural changes, `ast_edit` is used. The fix agent is constrained by its prompt to only edit the scoped files listed below.

4. **Records the fix:** Appends to `scripts/failures_log.jsonl` — `{scenario, failure, diagnosis, files_changed, patch_summary}`.

**What the fix agent CAN edit (scoped by the prompt, not hard-restricted):**
- `src/browser_agent/use_cases/explorer_system_prompt.py`
- `src/browser_agent/use_cases/discovery_writer_system_prompt.py`
- `src/browser_agent/use_cases/processing_writer_system_prompt.py`
- `src/browser_agent/use_cases/emitted_script_linter.py` (add/remove rules)
- `src/browser_agent/use_cases/zendriver_error_patterns.py` (add error patterns)
- `src/browser_agent/script_tools/*.py` (fix helper bugs)
- `src/browser_agent/use_cases/script_repair_prompt.py` (improve repair prompts)
- `src/browser_agent/configuration.py` (adjust timeouts/limits if needed)

**What it CANNOT edit:**
- `step_0_generate_script.py` driver orchestration (the pipeline structure is stable; only the prompts/tools/rules it consumes should change)
- `domain/*.py` pydantic models (the output contract is stable)
- `scripts/run_scenario.py` or `scripts/robustness_loop.py` (the test harness itself)

**Git checkpoint:** Before each patch, the runner creates a git stash or commit checkpoint. If the fix makes the scenario still fail AND a previously-passing scenario now fails, the patch is reverted. This is handled by the loop driver, not the fix agent itself.

### Step 6 — Create the loop driver

**File:** `scripts/robustness_loop.py`

The main loop that ties everything together:

```python
# Pseudocode (actual code uses constants, not args)
consecutive_passes = 0
scenario_queue = load_initial_scenarios()  # from scripts/fixtures/

for iteration in range(MAX_LOOP_ITERATIONS):
    scenario = scenario_queue.pop(0)

    # 1. Run the scenario
    result = run_scenario(scenario)

    if result.success:
        consecutive_passes += 1
        log_pass(scenario, iteration)
        if consecutive_passes >= CONSECUTIVE_PASSES_TO_STOP:
            break  # agent is robust enough
    else:
        consecutive_passes = 0
        # 2. Diagnose + fix
        for attempt in range(MAX_FIX_ATTEMPTS_PER_SCENARIO):
            git_checkpoint()
            diagnosis = fix_agent.diagnose(result)
            fix_agent.apply_patch(diagnosis)
            re_result = run_scenario(scenario)
            if re_result.success:
                log_fix_success(scenario, attempt, diagnosis)
                break
            else:
                git_revert_if_regression(re_result, result)
                if attempt == MAX_FIX_ATTEMPTS_PER_SCENARIO - 1:
                    log_fix_failure(scenario, diagnosis)

    # 3. Generate next harder scenario
    next_scenario = generate_next_scenario(failure_history)
    scenario_queue.append(next_scenario)
```

**Progression logic:** After each scenario (pass or fail), the scenario generator creates the next scenario at `difficulty = current_difficulty + 1` (capped at 8). If the current scenario failed and was fixed, the next scenario probes the *same pattern* but with a twist (e.g., if "dropdown filter" was fixed, next scenario has nested dropdowns or filter + pagination). If it passed, move to the next pattern.

**Regression guard:** After each fix, re-run the last 3 passing scenarios to ensure no regression. If any regresses, revert the patch.

**Logging:** All results go to `scripts/robustness_results.jsonl` — one JSON line per iteration with scenario, result, diagnosis, files changed, and outcome.

### Step 7 — Seed initial scenarios

**File:** `scripts/fixtures/` (directory)

Create 8 initial fixture directories (one per difficulty level from the table in Step 3). Each contains:
- `index.html` or a set of HTML pages
- `manifest.json` with the `RobustnessScenario` metadata
- For PDF scenarios: a small dummy PDF file

These are hand-written (by the implementation agent, not the LLM) to bootstrap the loop. The scenario generator (Step 3) creates additional fixtures as the loop progresses.

**Fixture HTML patterns (concrete):**

- **Level 1 (`single_page_list`):** One page with 10 items, each in `<div class="item">` with `<h3>` title, `<span class="date">`, `<a href="/doc/N">` link. No pagination, no filters.
- **Level 2 (`multi_page_pagination`):** 5 pages, each with 10 items. "Next" button links to `?page=N+1`. Last page has no Next button.
- **Level 3 (`dropdown_filter`):** A `<select id="filter-category">` with 4 options. Each option changes the item list. Items have category-specific URLs.
- **Level 4 (`infinite_scroll`):** One page that loads more items via AJAX when scrolling. Server serves `?page=N` JSON→HTML fragments. A "Load more" button as fallback.
- **Level 5 (`spa_dynamic`):** Content rendered by JavaScript (server sends empty `<div id="app">`, JS fills it). Tests `wait_for_page_ready` + `wait_for_anchors`.
- **Level 6 (`pdf_download_modal`):** Item pages with a "Download" button that reveals PDF links. Tests PDF download + classification.
- **Level 7 (`mixed_content`):** Items with both PDF and DOC/HTML links. Tests `file_ext_for`, document type handling.
- **Level 8 (`concurrency`):** 50+ items requiring parallel processing. Run config sets `parallel_runners: 4`. Tests asyncio.Queue, gate_lock, per-tab workers.

### Step 8 — Verification harness details

**File:** `scripts/verify_output.py`

After the emitted script runs, verify against `ExpectedOutput`:

```python
def verify(scenario: RobustnessScenario, run_path: Path) -> ScenarioResult:
    db_path = run_path / "metadata.db"
    if not db_path.exists():
        return ScenarioResult(success=False, failures=["metadata.db not found — save_record was never called"])

    conn = sqlite3.connect(db_path)
    # Schema: metadata(source_url TEXT PK, task_slug TEXT, scraped_at TEXT, data TEXT)
    # The `data` column holds a JSON blob with the scraped fields.
    rows = conn.execute("SELECT data FROM metadata").fetchall()

    checks = []
    if len(rows) < scenario.expected.min_records:
        checks.append(f"Expected >={scenario.expected.min_records} records, got {len(rows)}")

    for field in scenario.expected.required_fields:
        found = False
        for (data_json,) in rows:
            data = json.loads(data_json)
            val = data.get(field)
            if val is not None and str(val).strip() != "":
                found = True
                break
        if not found:
            checks.append(f"Field '{field}' is null/empty in all rows")

    pdf_dir = run_path / "downloads"
    pdf_count = len(list(pdf_dir.glob("*.pdf"))) if pdf_dir.exists() else 0
    if pdf_count < scenario.expected.pdf_count:
        checks.append(f"Expected >={scenario.expected.pdf_count} PDFs, got {pdf_count}")

    return ScenarioResult(success=not checks, failures=checks, ...)
```
**Reuse:** `save_record.py` writes to `metadata.db` with schema `metadata(source_url TEXT PK, task_slug TEXT, scraped_at TEXT, data TEXT)`. The `data` column is a JSON blob of scraped fields — the verifier parses it with `json.loads()` to check field values. The DB path is resolved via env var `BROWSER_AGENT_SAVE_RECORD_DB_PATH` or derived from `__main__.__file__` under `<run>/scripts/`.


### Step 9 — Wire up the loop runner entry point

**File:** `scripts/robustness_loop.py` (the main script)

Run via: `python scripts/robustness_loop.py`

The script:
1. Checks that `OLLAMA_API_KEY` is set (from `.env`)
2. Checks that Chromium is available at `/usr/bin/chromium`
3. Starts the fixture server as a background process
4. Runs the loop (Step 6)
5. On exit: stops fixture server, prints summary

## Critical Files & Anchors

1. **`src/browser_agent/drivers/step_0_generate_script.py:83-199`** — `GenerateScriptDriver` class: the pipeline entry point. The loop calls `GenerateScriptDriver().run([])` per scenario. Understanding the `_generate_and_verify` flow (explore → discover → process → lint → emit → smoke → repair) is essential for diagnosing where a scenario fails.

2. **`src/browser_agent/drivers/generation/script_smoke_tester.py:88-144`** — `smoke_test_script()`: runs the emitted script as a subprocess with a 60s timeout. The loop reuses this to run the emitted script with a longer timeout for full verification.

3. **`src/browser_agent/use_cases/emitted_script_linter.py`** — `EmittedScriptLinter.lint()`: 15 discovery rules + 22 processing rules. The fix agent adds/removes rules here when the LLM produces scripts that pass linting but fail at runtime.

4. **`src/browser_agent/use_cases/processing_writer_system_prompt.py`** — The 34.7KB system prompt that dictates how the Processing Writer generates scripts. This is the primary target for prompt-level fixes (new rules, clarified instructions, better examples).

5. **`src/browser_agent/script_tools/discover_links.py`** — `discover_links()`: the canonical scroll + load-more + terminate loop. If discovery fails on a scenario (infinite scroll, load-more button), the fix is either in this helper or in the discovery writer prompt that instructs the agent to call it correctly.

## Verification

### End-to-end proof

Run the loop for one full iteration with a level-1 scenario:

```bash
# From project root
python scripts/robustness_loop.py
```

Expected output:
```
[robustness] iteration 1: scenario=single_page_list difficulty=1
[robustness] starting fixture server on 127.0.0.1:8765
[robustness] running step_0 driver for robustness_single_page_list...
[robustness] driver completed (exit=0)
[robustness] running emitted script for verification...
[robustness] verifying output: 10 records found, 0 PDFs, required fields OK
[robustness] PASS
[robustness] generating next scenario (difficulty=2)...
```

### Failure-then-fix proof

Manually break a rule in the processing writer prompt (e.g., remove the instruction to call `save_record`), run the loop, and confirm:
1. The scenario fails (0 records in metadata.db)
2. The fix agent diagnoses the missing `save_record` call
3. The fix agent patches the prompt
4. Re-run passes

### Regression proof

After a fix, the loop re-runs the last 3 passing scenarios. If any regresses, the patch is reverted. Verify by: introducing a fix for scenario A that breaks scenario B, confirm the loop detects the regression and reverts.

### Convergence proof

Run the loop for up to 100 iterations. Confirm:
- Scenario difficulty escalates over time (starts at 1, reaches 5+)
- Pass rate increases (early iterations fail more, later ones pass)
- After 5 consecutive passes at difficulty ≥5, the loop stops
- `scripts/robustness_results.jsonl` contains a complete record of all scenarios, failures, diagnoses, and patches

## Assumptions & Contingencies

1. **LLM availability:** The loop uses the same `OllamaAdapter` + `deepseek-v4-flash` model as the generation pipeline. If the LLM is unavailable, the loop exits with a clear error. The fix agent and scenario generator both use `OllamaAdapter.get_model()` + `pydantic_ai.Agent(output_type=dict)` for structured one-shot calls.

2. **Chromium availability:** The loop needs `/usr/bin/chromium` for both the generation pipeline (explorer agent) and the emitted scripts. If headless mode is needed, set `ZENDRIVER_HEADLESS=true` in the environment. Contingency: if Chromium is missing, skip browser-dependent scenarios and log a warning.

3. **Fixture server port conflicts:** If port 8765 is taken, the server scans ports 8765–8775 and picks the first free one. The run config prompt uses the actual port.

4. **Fix agent produces bad patches:** Fully autonomous means no human review. Mitigations: (a) git checkpoint before each patch allows revert, (b) regression testing after each patch catches breakage, (c) the fix agent prompt constrains it to only edit the scoped files listed in Step 5, (d) max 3 fix attempts per scenario prevents infinite loops on unsolvable failures. If the fix agent consistently produces bad patches (3 consecutive scenarios fail all 3 attempts), the loop stops and logs a "needs human intervention" warning.

5. **Fixture HTML complexity:** Hand-written fixtures may be too simple to trigger real-world patterns. The scenario generator (Step 3) creates more realistic fixtures over time by reading failure logs and generating HTML that specifically exercises the failing pattern. If generated fixtures are unrealistic, the loop may pass trivially without real robustness — mitigated by escalating difficulty and the regression suite.

6. **`metadata.db` schema:** The verifier reads the `metadata` table with schema `(source_url TEXT PK, task_slug TEXT, scraped_at TEXT, data TEXT)` — the `data` column is a JSON blob, not separate columns. The verifier uses `PRAGMA table_info` to confirm the table exists, then `json.loads(data)` for field checks. Contingency: if `save_record` is never called (agent didn't use it), the DB doesn't exist → verifier reports 0 records → scenario fails → fix agent patches the prompt to enforce `save_record` usage.