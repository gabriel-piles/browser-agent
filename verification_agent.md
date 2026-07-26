# Improving the PDF download-verification agent (step 1)

## Context

`step_1_verify_downloaded_pdfs.py` is a thin driver; the actual verification behaviour lives in
`use_cases/verify_downloads_use_case.py`, `use_cases/verification_system_prompt.py`, and the four
bound tools (`verification_explore_tool`, `check_pdf_tool`, `query_db_tool`, `run_read_script_tool`).

The agent's stated job (`verification_system_prompt.py:15`) is to determine whether **every** PDF the
prompt requires was downloaded and is intact — *not a sample*. Today that guarantee cannot hold,
because the only per-PDF evidence path is an LLM loop capped at 10 checks, matching on exact URL
strings, with no independent expected-count. The suggestions below are ordered by how much they
change the answer to "did we get them all?".

**Requested scope: suggestions only. No code changes.**

---

## 1. Do the exhaustive part in code, not in the LLM loop

`VERIFICATION_PDF_COUNT = 10` (`configuration.py:18`) directly contradicts the prompt's
"You are NOT limited to a sample — check every candidate" (`verification_system_prompt.py:71-73`).
On a 500-PDF run the agent gets `_limit_reached` (`check_pdf_tool.py:114`) after 10 calls and is told
to emit the report — so the report is structurally a sample no matter what the prompt says.

Suggestion: split the work by what actually needs a model.

- **Deterministic reconciler (new use case, pure Python, always runs, no LLM).** For every row in
  `metadata.db`: recompute the expected on-disk name, stat the file, validate it, and diff both
  directions. Produces the full N-row inventory in one pass.
- **LLM stage** keeps only the part a model is needed for: re-walking the site to find PDFs that were
  *never discovered* (invisible to any DB-vs-disk diff), and root-causing gaps against the script source.
- `check_pdf` then becomes a spot-check for *newly discovered* candidates, and its limit of 10 stops
  being a correctness ceiling.

Write the reconciler output to disk *before* the agent runs, so a model failure mid-run still leaves
usable evidence (currently a crash at request 49 of `MAX_LLM_CALLS = 50` loses everything).

## 2. Derive the expected filename instead of trusting the DB

`check_pdf._check_file` (`check_pdf_tool.py:78`) reads `data["pdf_filename"]` — a field the *step-0 LLM*
was instructed to populate by hand (`emitted_pdf_download.py:76-80`). If it forgot, `pdf_filename` is
empty → `file_exists=False` → verdict `file_not_downloaded`, a **false alarm even though the file is on disk**.

But the name is a pure function of the URL: `pdf_<sha1(url)[:12]>.pdf`
(`PDF_FILENAME_SNIPPET`, `emitted_snippets.py:57-65`). So the verifier can compute it independently.
Recommended checks in the reconciler:

- expected path from `sha1(pdf_url)` — the authoritative existence test;
- **also try the `https://` form**: `download_pdf_browser` upgrades `http://`→`https://` *before*
  hashing (`emitted_pdf_download.py:337-343`), so a row that stored the original `http` URL hashes to a
  name that is not on disk. Real false-negative source; try both forms and report which matched;
- DB `pdf_filename` vs computed name **mismatch** → report as a step-0 bug in its own right;
- **orphan files** in `downloads/` with no DB row (PDF downloaded, metadata lost) — currently invisible,
  since every check starts from a URL the agent already has;
- **`.part` leftovers** — `_write_atomic` names temp files `<name>.part` (`emitted_snippets.py:20`), so a
  stray `.part` is direct evidence of a crashed mid-download;
- duplicate `pdf_url` rows, and rows with empty/missing `pdf_url`.

## 3. Normalize URLs before matching — likely the top false-alarm source

`_query_db` matches exact string equality: `json_extract(data,'$.pdf_url') = ?` (`check_pdf_tool.py:61`).
The candidate URLs come from `explore_page` page snapshots, so they routinely differ from the stored
form by scheme (see the http→https rewrite above), trailing slash, percent-encoding, query-param order,
session tokens, or relative-vs-absolute resolution. Every such difference reports `missing_from_db` for a
PDF that was downloaded perfectly.

Suggestion: normalize both sides (lowercase scheme+host, upgrade `http`→`https` to match the downloader,
drop fragment, sort query params, optionally strip known volatile ones), fall back to a
path/basename suffix match, and **return which match mode succeeded** so the agent can distinguish
"genuinely absent" from "recorded under a different URL form". Keep the normalizer in one place so the
reconciler and `check_pdf` cannot drift.

## 4. Strengthen the integrity check

`_is_valid_pdf` is `%PDF` magic + `size > 1024` (`check_pdf_tool.py:97-102`). This misses the most common
real corruption and produces one false positive class:

- **truncated download passes** — a connection dropped at 60% still starts with `%PDF` and is 200 KB.
  Check for `%%EOF` in the last ~2 KB. (`pypdf` would give page counts but is *not* a dependency —
  `pyproject.toml:7-18`; the `run_read_script` docstring's "pypdf (if installed)" is aspirational.)
- **identical-size clusters** — N files with the exact same byte size is a strong signal the same error
  page or placeholder was saved N times. A per-URL check can never see this; a whole-corpus pass can.
  Worth flagging as its own finding.
- **a legitimate PDF under 1 KB is reported `corrupt_file`.** Separate "invalid" (magic/EOF failed) from
  "suspiciously small" (size outlier) so the operator can tell a real corruption from a small-but-fine file.
  Also `_verdict` re-tests `file_size <= _MIN_VALID_SIZE` (`check_pdf_tool.py:109`) after `_is_valid_pdf`
  already did — collapse that.

## 5. Give the report a denominator

`VerificationReport` has `missing_count` but no *expected* total, and `overall_assessment` is free prose
(`domain/verification_report.py:19-36`). "Were all PDFs detected?" is unanswerable without an
independently-derived expectation, and nothing downstream can gate on prose.

Suggestions:

- Make the agent commit to an **expected inventory before checking**: for each prompt-described path,
  harvest the site's own advertised count via `explore_page` ("1,234 results", "Page 1 of 57") and record it.
- Add `expected_total` / `observed_total` to `MissingCoverage`, and an overall `expected_pdf_total` plus a
  boolean `coverage_complete` (or a confidence enum) to `VerificationReport`. Per `AGENTS.md`, these stay
  pydantic models under `domain/`, one class per file.
- The site-advertised count vs `SELECT COUNT(DISTINCT pdf_url)` comparison per path is the single most
  useful number the report could carry, and it is cheap.

## 6. Make prompt→path decomposition an explicit artifact

The prompt asks the agent to build a *"mental model of every navigation path/filter/page"*
(`verification_system_prompt.py:57-59`) and then check coverage against it. With a broad prompt
(all years × all states × all subcategories) that mental model silently truncates, and nothing detects
the truncation — the biggest recall risk for *detection* as opposed to *download*.

Suggestion: make it a first-class artifact instead. Either a separate cheap LLM call that emits a
structured `expected_paths` list up front, or a required first tool call that stores the declared paths on
`VerificationAgentDeps` and echoes the remaining unvisited ones in every subsequent tool return.
Coverage then becomes an auditable checklist with a denominator rather than a judgement call.

## 7. Stop having the LLM transcribe tool output

`verification_system_prompt.py:94-99` instructs the model to retype every field from `check_pdf`'s text
return into a `PdfCheckResult`. Meanwhile `check_pdf` *already builds* the real `PdfCheckResult`
(`check_pdf_tool.py:84`) and discards it in favour of formatted text (`_format_result`).

Suggestion: accumulate the real objects on `VerificationAgentDeps` (it already carries mutable counters,
`verification_agent_deps.py:28-31`) and have the driver splice them into the report after the run — or at
minimum validate the LLM's list against them. Removes a hallucination surface, saves output tokens, cuts
~15 lines of prompt, and makes the table in `verification_report.md` ground truth.

## 8. Make gaps come from the gap map

`ScrapingGapMapBuilder` only reports what is *present*, and `_render_field` dumps **every** distinct value
with no cap (`scraping_gap_map_builder.py:66-70`) — on a large DB that is a huge prompt — while source
anchors are capped at 20 and only shown when no field counts exist at all
(`scraping_gap_map_builder.py:62-63`).

Suggestions: cap per-field values; and compute actual *gaps* rather than a census — holes in dense numeric
ranges (`year` 2019 and 2021 present, 2020 absent) and zero-row cells in the year × state cross-product.
That surfaces "the filter loop skipped a value" directly, instead of asking the model to infer it from
a distribution table.

## 9. Install the compactor on this agent

`VerifyDownloadsUseCase._build_agent` (`verify_downloads_use_case.py:38-45`) omits
`capabilities=[ToolReturnCompactor()]`, which step 0 does install
(`generate_zendriver_script_use_case.py:49`). With `SNAPSHOT_MAX_CHARS = 50_000` per `explore_page` return
and `MAX_LLM_CALLS = 50`, a genuinely thorough re-walk will exhaust the context and degrade the report.
Cheapest single change here, and it directly raises how deep the agent can walk.

## 10. Let the result reach the pipeline

`_run_async` returns `0` unconditionally (`step_1_verify_downloaded_pdfs.py:69`) even when the report says
every PDF is missing, and `verification_report.md` is markdown-only — so the `step_0_fix` handoff that
`MissingCoverage` is designed for has to be re-typed by a human.

Suggestions:

- Distinct exit codes: `0` clean, `1` gaps found (`missing_count > 0` or non-empty `missing_coverage`),
  `2` could not run (currently the missing-`scripts/` path also returns `1`,
  `step_1_verify_downloaded_pdfs.py:95-104`).
- Also emit `verification_report.json` beside the `.md` so step 0 can consume `missing_coverage`
  programmatically as its next prompt — closing the loop the report was designed for.

## 11. Smaller consistency items

- `check_pdf._query_db` opens the DB **read-write** (`sqlite3.connect(str(db_path))`,
  `check_pdf_tool.py:58`), as does `metadata_db.query_rows` (`metadata_db.py:22`), while `query_db`
  correctly uses the `mode=ro` URI (`query_db_tool.py:59`). For tools whose contract is READ-ONLY, use the
  `ro` URI everywhere.
- `query_db` has no call limit while `check_pdf` and `run_read_script` both do — an agent can burn all 50
  requests on SELECTs.
- `check_pdf` increments `pdf_checks` *before* the limit test (`check_pdf_tool.py:35-37`), so the counter
  keeps climbing past the cap. Harmless today, but it makes the counter useless for reporting.
- No retry around `agent.run` (`verify_downloads_use_case.py:66`); a transient model error loses the whole
  verification and no report is written. Less pressing once #1 persists reconciler output first.

---

## Suggested order if this gets implemented later

1. **#9** (compactor) and **#11** — minutes, no design decisions.
2. **#1 + #2 + #3** — the deterministic reconciler with URL normalization and computed filenames. This is
   the change that makes "all PDFs" a real claim rather than a sampled one.
3. **#4 + #5** — integrity depth and the expected/observed denominator.
4. **#7 + #10** — trustworthy report contents and a machine-readable handoff to step 0.
5. **#6 + #8** — recall improvements for PDFs that were never *detected*.

## Verification (when implemented)

- Run `python -m browser_agent.drivers.step_1_verify_downloaded_pdfs` against a run directory and confirm
  the reconciler section of `verification_report.md` accounts for **every** DB row, not 10.
- Seed deliberate faults in a scratch run and confirm each is reported with the right verdict: delete one
  downloaded file; truncate one PDF mid-file (keeps `%PDF`, loses `%%EOF`); blank one row's
  `pdf_filename`; add an orphan file to `downloads/` with no row; leave a `.part` file; store one row's
  `pdf_url` as `http://` where the file was hashed from `https://`.
- Confirm exit codes: clean run → `0`, seeded-fault run → `1`, missing `scripts/` → `2`.
