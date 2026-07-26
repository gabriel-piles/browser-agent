# Improving the script-generation agent (step 0)

## Context

`step_0_generate_script.py` is a thin driver. The behaviour lives in
`use_cases/generate_zendriver_script_use_case.py`, `use_cases/system_prompt.py` (761 lines), the three
bound tools (`explore_page_tool`, `run_validation_script_tool`, `download_pdf_tool`),
`use_cases/tool_return_compactor.py`, the in-process runner
(`adapters/execution/in_process_script_runner_adapter.py`), and the emit chain under
`drivers/generation/`.

The agent's contract is: explore → write ONE validation script → run it (**3 attempts, hard cap**) →
emit the final script. Everything therefore depends on the agent *seeing* what its tools returned.
Three separate mechanisms currently destroy exactly that evidence before the model reads it, and the
prompt's HARD rules are enforced only by a 60-second runtime smoke test whose result never reaches the
agent nor the exit code. Ordered by impact below.

**Requested scope: suggestions only. No code changes.**
Companion document for step 1: `verification_agent.md`.

---

## 1. The compactor trims the *newest* returns while there are few of them — inverted logic

`_cut_index` (`tool_return_compactor.py:165-169`) returns "the lowest index to KEEP", and
`_maybe_rewrite` trims when `idx < cut` (`:175-180`). So "keep everything" must be cut `0`. But the
early-exit returns `_NEVER_TRIM = 10**9`:

```python
if keep_recent <= 0 or len(indices) <= keep_recent:
    return _NEVER_TRIM        # → idx < 10**9 is always true → trims EVERYTHING
```

The constant's name says the intent was "so high nothing qualifies", but with a `idx < cut`
comparison a huge cut trims every candidate. Consequences, both in the regime that matters most:

- **`explore_page`**: `COMPACT_KEEP_RECENT_STRUCTURED = 5`. With ≤5 large snapshot returns, *all* of
  them get the HTML body replaced by `COMPACT_TRUNCATED_PLACEHOLDER` — including the one just
  returned. So during Steps 1-3, where the prompt says *"Read the returned HTML carefully"*
  (`system_prompt.py:69`) and *"see the surrounding DOM structure"* (`:92`), the agent never receives
  the HTML. It only starts seeing full snapshots from the 6th large explore return onward.
- **`run_validation_script`**: `COMPACT_KEEP_RECENT_VALIDATIONS = 1`. The first validation return is
  cut to `COMPACT_HEAD_LINES = 6` non-empty lines by `_summarise_generic` (`:238-248`) — and only
  returns over `COMPACT_MIN_TRIM_CHARS = 1000` are affected, i.e. precisely the informative ones. With
  a hard cap of 3 attempts, attempt 1's output effectively does not exist for the model.

Suggestion: return `0` (or invert the constant) so "few enough to keep" keeps them, and verify the
kept-window arithmetic with a table of `(n_returns, keep_recent) → expected trimmed set`. This is a
one-line change and the highest-leverage item in step 0 — it restores the evidence every later step
in the prompt assumes the agent has. Note also `COMPACT_KEEP_RECENT_SNAPSHOTS` is imported
(`:37`) but never used; `snap_cut` uses `COMPACT_KEEP_RECENT_STRUCTURED` instead — decide which one is
intended and drop the other.

## 2. A validation timeout discards all partial output *and* burns an attempt

In `InProcessScriptRunnerAdapter.run` the `_redirect_stdio` buffer lives *inside* the coroutine that
`asyncio.wait_for` cancels (`in_process_script_runner_adapter.py:100-111`, `:127`). On timeout the
`finally` restores stdout and the buffer is discarded, so the tool returns only
`[TIMEOUT after 90s — validation script cancelled]`.

A script that printed 40 lines of counts, sample hrefs, and label-vs-badge comparisons — exactly what
Step 7 mandates (`system_prompt.py:126-154`) — and then ran a few seconds long, returns **zero
diagnostics** and consumes one of three attempts. On a slow target site this is the most likely path to
"3 attempts exhausted, emit an unvalidated script".

Suggestion: own the buffer at the `run()` level so partial stdout survives cancellation, and return it
with the timeout notice. Consider also not charging a timeout as a full attempt when no output was
produced, or raising `VALIDATION_TIMEOUT_S` (currently 90 s, vs `SMOKE_TEST_TIMEOUT_S = 60`) — a
validation script that navigates, clicks a filter, scrolls, downloads 2 PDFs *and* saves HTML has very
little headroom in 90 s.

## 3. On failure, everything the validation script printed is thrown away

`_extract_error` returns `output[idx:]` from the **last** `Traceback` marker
(`run_validation_script_tool.py:91-95`), so every line printed before the crash is dropped. But Step 7
is built entirely around those prints — counts, sample hrefs, `attr title:` vs `inner text:`, the
SUCCESS/FAIL summary. A script that successfully proved 8 of 9 checks and crashed on the 9th reports
only the traceback; the agent cannot tell what already worked and re-tests everything on the next of
its 2 remaining attempts.

Also, a validation script that follows rule 13's "wrap in `try/except RuntimeError` to keep going"
prints *many* tracebacks; only the last survives, which may be the least informative.

Suggestion: keep a head slice (the prints) **and** the last traceback, with a marker between them —
`_summarise_generic` already establishes the head-keeping idiom. Compounding with #1: today attempt 1
returns 6 lines of a traceback with no context.

## 4. Nothing deterministically checks the emitted script before it is written

The prompt states ~14 HARD rules. None are machine-checked. `GeneratedScript.has_async_main()`
(`domain/generated_script.py:79`) exists and **is never called anywhere** — verified by grep; only
`line_count()` and `dependency_names()` are used, for a log line
(`generate_zendriver_script_use_case.py:85-86`). The emitter applies 8 string transforms and writes the
file (`script_emitter.py:59-68`), and the only real gate is a 60-second smoke test *after* the run is over.

A pure-Python lint pass over `python_code` — no LLM, instant — could catch the exact bug classes the
prompt spends its length warning about:

| Rule | Deterministic check |
| --- | --- |
| `save_record` is sync (`:263-268`, called "the recurring bug class") | `await save_record(` |
| result dicts have no `file_size` (`:269-272`) | `["file_size"]` / `.get("file_size")` |
| never `zd.start()` (`:276`) | `zd.start(` surviving the rewrite |
| no HTTP libraries (rule 8, `:506`) | `ast` import walk for requests/httpx/aiohttp/urllib |
| standard CSS only (rule 7, `:496`) | `:has-text(`, `:text=`, `:visible`, `:has(` |
| `tab.evaluate` must be an expression or IIFE (rule 10, `:561`) | bare `() => {` argument not wrapped in `(...)()` |
| output paths relative to `__file__` (rule 12, `:611`) | bare `Path("downloads")` / `"./downloads"` |
| self-contained (rule 5) | any `browser_agent.` import except `runtime_helpers` |
| valid Python at all | `compile(code, "<emitted>", "exec")` |

Two ways to use it, both worth having: gate before `ScriptEmitter.emit` writes the file, and feed
violations back to the agent as a **repair turn that does not consume a validation attempt** (the
attempt budget should pay for browser work, not for syntax). Per `AGENTS.md`, this fits as a small
class per check or one `EmittedScriptLinter` under `use_cases/` with the findings as a pydantic model in
`domain/`.

## 5. The smoke test's result reaches nobody, and it dirties the run

`_run_async` awaits `self._smoke_test(script_path)` then `return 0` unconditionally
(`step_0_generate_script.py:69-70`); `log_smoke_test_result` only logs
(`script_smoke_tester.py:78-91`). So a script that crashes on import is written to disk, reported as
emitted, and exits `0`. Step 11 of the prompt tells the model the framework enforces this
(`system_prompt.py:179`) — nothing is enforced.

Three separate suggestions:

- **Feed the failure back.** The smoke test exists precisely to catch bugs that only appear in the
  final form (`script_smoke_tester.py:9-13`) — stripped imports shadowing vendored helpers, JS string
  concatenation, missing helper definitions. Those are cheap for the model to fix if it sees the
  traceback. One bounded repair turn on FAIL, then re-emit and re-smoke-test.
- **Exit non-zero on FAIL** (and keep a distinct code for "could not run at all"), so the operator and
  any pipeline can react instead of reading logs.
- **The 60-second window runs the real scraper against the real run directory.** The emitted
  `save_record` derives its DB path from `__file__` (`emitted_save_record.py`, "Path resolution"), so
  the smoke test writes real rows into `<run>/metadata.db` and real PDFs into `<run>/downloads/`, then
  gets killed mid-flight. That leaves a partial, unexplained state that **step 1 then has to verify**,
  and it silently consumes the idempotency skip-by-path. Consider pointing the smoke test at a scratch
  DB/downloads dir via injected globals (the in-process runner already does exactly this with
  `_SAVE_RECORD_DB_PATH`, `in_process_script_runner_adapter.py:193`), or bounding it to a
  navigate-and-exit dry-run mode.
- Related: `proc.kill()` (`script_smoke_tester.py:68`) SIGKILLs the Python process, but the Chromium
  the script launched is a child and typically survives. Every step-0 run can leave an orphaned browser
  (with the run's profile locked). Launch with `start_new_session=True` and kill the process group.

## 6. Prompt hygiene — 761 lines, with visible accretion damage

`SYSTEM_PROMPT` is roughly 9-10k tokens resent on every one of up to `MAX_LLM_CALLS = 50` requests. Its
size is defensible given how much hard-won zendriver lore it encodes, but it has concrete defects that
degrade instruction-following:

- **Rule 8 is truncated mid-sentence.** `system_prompt.py:510-512`: *"All fetching, navigation and API
  calls go through ``tab.get(url)`` and, when a page needs to hit an"* → next line begins
  *"EXCEPTION — PDF downloads."* The indentation also drops from 3 spaces to 2, so this looks like a bad
  merge that ate the rest of the sentence (presumably about XHR/API endpoints).
- **Two rules numbered `4b`** — `:343` (null-guard `tab.evaluate`) and `:464` (label-vs-badge) — and
  `4a` appears *after* the first `4b` (`:368`). Rule 13 references "rule 13" from inside Step 7
  (`:150`), so the numbers are load-bearing.
- **Label-vs-badge is stated three times** (Step 7 `:138-146`, rule 4a(a) `:388-394`, rule 4b
  `:464-479`) with slightly different wording; rule 4b's example calls `row.querySelector(...)` —
  JS syntax on a Python handle, contradicting rule 4a's own `el.apply` guidance.

Suggestion: renumber, restore the lost clause in rule 8, and collapse the triplicated guidance into one
rule referenced from Step 7. Separately, consider splitting the ~450 lines of zendriver API lore
(rules 0, 4, 4a, 4b, 7, 9, 10) into a reference section the model is told to consult, keeping the
workflow prompt short — the workflow steps are what need to be salient on every turn.

## 7. Exploration has no budget, validation has a hard one

`run_validation_script` counts attempts and reports remaining budget in every return
(`run_validation_script_tool.py:74-78`). `explore_page` has **no** counter and no budget signal, yet it
draws from the same `MAX_LLM_CALLS = 50` pool. An agent can spend 40 requests exploring and reach Step 7
with nothing left; nothing in any tool return tells it how much room remains.

Suggestion: mirror the step-1 pattern — a counter on `AgentDeps` and a footer like
"exploration call 22; ~N requests remain before you must emit". Cheap, and it makes the pacing the
prompt asks for actually observable to the model.

## 8. Same-day, same-task runs silently overwrite the previous script

`ScriptPathBuilder.build` returns `{today}__{slug}.py` where the slug is the first 6 words of the task
(`script_path_builder.py:22-38`). Iterating on one prompt twice in a day overwrites the earlier script
in place — and step 1 reads only the newest by mtime
(`step_1_verify_downloaded_pdfs.py:101`), so the prior attempt is unrecoverable. The docstring's claim
that the date prefix means "a single day does not overwrite its peers" holds only across *different*
tasks. Suggestion: add `%H%M%S` or a `__002` suffix on collision.

## 9. Persist the artifacts step 1 needs (and the operator's only explanation)

`ScriptEmitter._print_payload` dumps the whole `GeneratedScript` — including full `python_code` — to
**stdout** via `print` (`script_emitter.py:74-79`), interleaved with loguru's stderr logs. Nothing is
persisted except the `.py`. So:

- `explanation` — the model's account of selectors, scroll strategy, and mutation order — exists only
  in terminal scrollback.
- `pdf_download_strategy` is likewise transient, even though it determines which helper the script uses.
- Step 1 reads only the `.py` source (`_read_latest_script`), so its root-cause analysis works without
  the explanation or the declared strategy that would explain *why* the script does what it does.

Suggestion: write a sidecar `<run>/scripts/<same-name>.json` with `explanation`, `dependencies`,
`pdf_download_strategy`, the lint findings from #4, and the smoke-test result; drop `python_code` from
the printed payload (it is already the file). Then have step 1's `_build_request` include the sidecar —
a direct, cheap improvement to step 1's report quality.

## 10. A failure anywhere after the LLM run loses the entire run

`_run_async` (`step_0_generate_script.py:55-70`) has no error handling around a generation run that
costs dozens of requests and many minutes. If `_finalize_source`'s 8 chained regex transforms raise, or
the write fails, the `GeneratedScript` is gone. Suggestion: persist the raw LLM `python_code`
immediately on return, *before* the transform chain, and treat the transformed file as derived. Also
worth logging which transforms actually matched — a rewrite that silently no-ops (e.g.
`with_emitted_normalize_launch` not finding `zd.start`) currently looks identical to one that applied.

## 11. Smaller items

- `self._path_builder` / `self._emitter` are `... | None` (`step_0_generate_script.py:46-48`) and used
  unguarded at `:67`. Safe at runtime because `_wire_run` precedes it, but `basedpyright` (a declared
  dev dependency) must be flagging it. Constructing them in `_run_async` from `run_path` removes the
  Optional entirely.
- The driver parses `sys.argv` and `--stdin` via `TaskReader`, while `AGENTS.md` says *"Do not use
  arguments in scripts, use constants in the script"* — and the `DEFAULT_PROMPT` comment at `:35-38`
  cites that policy *immediately above* the argv-reading class. Worth deciding which way this goes;
  `run.prompt` from `run.yaml` already covers the real use case.
- `_redirect_stdio` (`in_process_script_runner_adapter.py:317-328`) swaps the global `sys.stdout` /
  `sys.stderr` for the duration of the validation script. Safe today (loguru binds its sink object at
  configure time), but it is a process-global mutation around LLM-authored code — worth a comment
  noting the dependency, since a future lazily-resolving sink would start leaking framework logs into
  the agent's validation output.
- LLM-authored validation code runs **in the driver's own event loop**. `asyncio.wait_for` cannot
  interrupt a synchronous block (`while True: pass`, a blocking `time.sleep`, a blocking C call), so
  such a script hangs step 0 forever with no timeout — a hazard the previous subprocess design did not
  have. The docstring documents the self-containment trade-off (`:20-25`) but not this one. A watchdog
  on a separate thread, or running the LLM's `main()` in a worker thread with its own loop, would bound it.

---

## Suggested order if this gets implemented later

1. **#1** — one line, and it is currently blinding the agent during exploration and on its first
   validation attempt. Fix and re-measure before changing anything else; several downstream symptoms
   ("wrong selectors", "wastes attempts") may simply be this.
2. **#2 + #3** — stop discarding validation evidence. Same theme, both small.
3. **#4** — the deterministic lint gate plus a non-attempt-consuming repair turn. Biggest structural win.
4. **#5** — make the smoke test matter: feed it back, gate the exit code, stop it dirtying the run,
   don't orphan Chromium.
5. **#9 + #8 + #10** — artifact durability, then **#6 + #7** prompt and pacing.

## Verification (when implemented)

- For #1, the fastest check is a unit-level table over `_cut_index` / `_maybe_rewrite`; end-to-end,
  run a generation and confirm the model's first post-navigate request contains real HTML rather than
  `[trimmed — see latest snapshot]`.
- For #2/#3, force a validation script that prints 20 lines then (a) raises and (b) sleeps past the
  timeout; confirm both tool returns carry the prints.
- For #4, hand the linter known-bad scripts — `await save_record(...)`, `result["file_size"]`,
  `import requests`, `a:has-text("x")`, `Path("downloads")`, `tab.evaluate("() => {...}")` — and
  confirm one finding each with the rule number.
- For #5, emit a script that raises on import and confirm a non-zero exit, a repair attempt, and no
  new rows in `<run>/metadata.db`; check `pgrep -f chrome` is clean after the run.
- End-to-end: `python -m browser_agent.drivers.step_0_generate_script` on a real run, then
  `step_1_verify_downloaded_pdfs` against it, confirming step 1 picks up the new sidecar JSON.
