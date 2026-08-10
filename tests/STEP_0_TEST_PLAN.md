# Step 0 Bulletproofing Test Plan

## Target

Bulletproof `src/browser_agent/drivers/step_0_generate_script.py` — the
end-to-end driver that orchestrates the three-agent pipeline:

1. **Explorer** agent → explores site, produces `TaskSplit` (decides
   `needs_discovery`, writes `discovery_prompt` + `processing_prompt`,
   collects `sample_document_urls`, probes `pdf_download_strategy`).
2. **Discovery Writer** agent → generates a discovery script (link
   collection across filters / pagination / scroll). Skipped when
   `needs_discovery` is false.
3. **Processing Writer** agent → generates a processing script
   (metadata extraction + PDF download via `save_record`).

Post-generation pipeline: **lint repair loop** (max 1 turn) → **emit**
→ **smoke test** (max 1 repair) → **discovery self-check** (UNDER-COLLECTED
repair, max 2 turns) → **independent audit** (`DiscoveryAuditor`) →
**cleanup emit artifacts**.

## Current Coverage (7 tests)

| Test file | Scenario | What it covers |
|---|---|---|
| `test_single_page_list` | `single_page_list` | Basic extraction, 10 items, CSS selectors, unique source_url |
| `test_multi_page_pagination` | `multi_page_pagination` | Next-button pagination, 50 items across 5 pages |
| `test_missing_pdfs` | `missing_pdfs` | 404 PDF downloads, graceful failure, download_status field |
| `test_mixed_extensions` | `mixed_extensions` | Case-insensitive .pdf/.PDF/.doc/.DOC, 6 PDFs downloaded |
| `test_slow_responses` | `slow_responses` | 3s server delays, wait timeout resilience |
| `test_cloudflare_protection` | `cloudflare_protection` | JS challenge (503 → redirect), browser executes JS |
| `test_large_scale_3000` | `large_scale_3000` | 3000 docs / 150 pages, dropdown + prev/next, ≥100 records |

## Existing Fixtures Without Tests (6 scenarios — quick wins)

These fixture scenarios already exist under `scripts/fixtures/` but have
no corresponding test. Each needs only a test file (no new fixture work).

### 1. `test_spa_dynamic_rendering` — SPA with delayed JS rendering

- **Fixture**: `spa_dynamic/index.html` — 10 items rendered after a
  1500ms `setTimeout`; no `_dynamic.py` (pure static + JS).
- **Tests**: Agent handles client-side rendering; `wait_for_page_ready`
  / `wait_for_anchors` wait for the `setTimeout` to populate the DOM
  before extracting. 10 records, `title` + `date` non-null.
- **Pipeline stress**: Explorer must see the JS-rendered DOM; Processing
  Writer must wait for dynamic content.
- **Asserts**: `assert_driver_success`, `assert_min_records(10)`,
  `assert_fields_non_null(["title", "date"])`.

### 2. `test_infinite_scroll_load_more` — AJAX load-more button

- **Fixture**: `infinite_scroll/_dynamic.py` — 10 items initially, a
  "Load more" button fetches `/fragment/?page=N` via `fetch()`, appends
  HTML. After 3 pages (30 items) the button disappears.
- **Tests**: Agent generates a script that clicks "Load more" until the
  button is gone, collecting all 30 items.
- **Pipeline stress**: Discovery Writer must handle AJAX-loaded content
  + button-click loop + termination condition (button removal).
- **Asserts**: `assert_driver_success`, `assert_min_records(30)`,
  `assert_fields_non_null(["title", "date"])`.

### 3. `test_dropdown_filter_iteration` — Multiple filter values

- **Fixture**: `dropdown_filter/_dynamic.py` — `<select>` with 4
  categories (reports/resolutions/measures/decisions), 5 items each
  (20 total). Changing the filter navigates to `?category=value`.
- **Tests**: Agent iterates all 4 filter values, collects 20 records,
  each with the correct `category` field.
- **Pipeline stress**: Explorer must identify the `<select>` as a
  filter; Discovery Writer must iterate all `<option>` values; the
  `DiscoveryAuditor` cross-check must verify all filter values were
  visited.
- **Asserts**: `assert_driver_success`, `assert_min_records(20)`,
  `assert_fields_non_null(["title", "category"])`.

### 4. `test_concurrency_parallel_runners` — parallel_runners=4

- **Fixture**: `concurrency/_dynamic.py` — 50 items, each linking to
  a detail page. Designed for `parallel_runners=4`.
- **Tests**: `run_generation_pipeline(..., parallel_runners=4)`; agent
  generates a script that processes detail pages concurrently.
- **Pipeline stress**: Processing Writer must emit a script compatible
  with `parallel_runners=4`; `save_record` must be thread-safe; no
  duplicate records from concurrent detail-page visits.
- **Asserts**: `assert_driver_success`, `assert_min_records(50)`,
  `assert_fields_non_null(["title", "date"])`.

### 5. `test_pdf_download_modal` — PDFs behind a modal/interstitial

- **Fixture**: `pdf_download_modal/index.html` + 5 PDF files — items
  link to PDFs; the fixture may have an interstitial.
- **Tests**: Agent downloads all 5 PDFs, saves 5 records with
  `download_status="downloaded"`.
- **Pipeline stress**: Processing Writer's PDF download strategy
  (`browser_fetch` or `curl_cffi`) handles the modal scenario.
- **Asserts**: `assert_driver_success`, `assert_min_records(5)`,
  `assert_pdf_count(5)`, `assert_fields_non_null(["title"])`.

### 6. `test_mixed_content` — Mixed PDF + HTML content

- **Fixture**: `mixed_content/index.html` + 5 PDF files — items link to
  PDFs.
- **Tests**: Agent downloads all 5 PDFs, saves records for each.
- **Pipeline stress**: Distinguish PDF links from HTML links; download
  only PDFs.
- **Asserts**: `assert_driver_success`, `assert_min_records(5)`,
  `assert_pdf_count(5)`.

---

## New Scenarios (require new fixtures + tests)

Organized by the pipeline dimension they stress. Each scenario needs a
new `_dynamic.py` (or `index.html`) under `scripts/fixtures/<name>/` and
a new `test_<name>.py` under `tests/`.

### A. TaskSplit / Explorer Decision Boundary

#### 7. `test_no_discovery_needed` — single page, `needs_discovery=false`

- **Fixture**: `no_discovery/` — 10 items on one page, no pagination,
  no filters, no scroll. Simplest possible page.
- **Tests**: Verify the Explorer correctly sets `needs_discovery=false`;
  the Discovery Writer is skipped (no `__discover__` script emitted);
  the processing script alone extracts all 10 items.
- **Pipeline stress**: `needs_discovery` decision; no discovery script
  emitted; `GeneratedScriptSet.from_scripts(None, processing_script)`;
  the discovery self-check and audit are skipped.
- **Asserts**: `assert_driver_success`, `assert_min_records(10)`, no
  discovery script in `run_path/scripts/`.

#### 8. `test_discovery_needed_pagination` — multi-page, `needs_discovery=true`

- **Fixture**: `discovery_needed/` — 3 pages, 10 items each, Next
  button. Same structure as `multi_page_pagination` but simpler.
- **Tests**: Verify `needs_discovery=true`; a discovery script is
  emitted and runs; the processing script consumes discovered links.
- **Pipeline stress**: Full discovery + processing pipeline; discovery
  self-check runs; `DiscoveryAuditor` runs.
- **Asserts**: `assert_driver_success`, `assert_min_records(30)`,
  discovery script exists in `run_path/scripts/`.

#### 9. `test_detail_page_extraction` — list page → detail pages

- **Fixture**: `detail_page/` — listing page with 10 items, each
  linking to a detail page `/doc/N` with full metadata (title, date,
  author, description, PDF link).
- **Tests**: Agent generates a script that navigates to each detail
  page, extracts all fields, and downloads the PDF.
- **Pipeline stress**: Discovery Writer collects detail-page links;
  Processing Writer navigates to each, extracts multi-field metadata,
  downloads PDF.
- **Asserts**: `assert_driver_success`, `assert_min_records(10)`,
  `assert_pdf_count(5)`, `assert_fields_non_null(["title", "date",
  "author", "description"])`.

### B. Pagination Variants

#### 10. `test_numbered_pagination` — page number links (1 2 3 4 5)

- **Fixture**: `numbered_pagination/` — 5 pages, 10 items each. Nav
  has numbered links `?page=1..5` plus Prev/Next. No dropdown.
- **Tests**: Agent uses numbered links or Next button; collects 50
  records.
- **Pipeline stress**: Discovery Writer handles numbered pagination
  (not just Next button).
- **Asserts**: `assert_min_records(50)`.

#### 11. `test_cursor_pagination` — cursor/offset-based pagination

- **Fixture**: `cursor_pagination/` — pages use `?after=<last_id>`
  (cursor-based, like GraphQL APIs). 3 pages, 10 items each. No
  Next/Prev buttons — the script must construct the next URL from the
  last item's ID.
- **Tests**: Agent generates a script that follows cursor-based
  pagination.
- **Pipeline stress**: Non-standard pagination; Explorer must
  understand cursor mechanics; Discovery Writer must loop until no more
  results.
- **Asserts**: `assert_min_records(30)`.

#### 12. `test_infinite_scroll_no_button` — scroll-to-load (no button)

- **Fixture**: `scroll_load_no_button/` — 30 items total; 10 visible,
  more loaded by scrolling to the bottom (IntersectionObserver or
  scroll event). No "Load more" button.
- **Tests**: Agent generates a script that scrolls until no new items
  appear, collecting all 30.
- **Pipeline stress**: Discovery Writer must scroll (not click) and
  detect termination via stale item count.
- **Asserts**: `assert_min_records(30)`.

#### 13. `test_pagination_disabled_next` — Next button with `disabled` class

- **Fixture**: `disabled_next/` — 5 pages; the Next button is present
  on the last page but has `class="next disabled"` and no `href`.
- **Tests**: Agent detects the disabled state and stops; no infinite
  loop; 50 records.
- **Pipeline stress**: Discovery Writer must check for disabled state,
  not just button absence.
- **Asserts**: `assert_min_records(50)`.

#### 14. `test_pagination_relative_urls` — relative href (no urljoin)

- **Fixture**: `relative_urls/` — 3 pages; Next button href is
  `page2.html` (relative), not `?page=2`. Detail links are `doc/N`
  (relative).
- **Tests**: Agent uses `urljoin` to construct absolute URLs; records
  have valid `source_url` starting with `http://127.0.0.1:{PORT}/`.
- **Pipeline stress**: URL construction correctness; `source_url`
  must be absolute.
- **Asserts**: `assert_min_records(30)`, verify all `source_url`
  values start with `http://127.0.0.1`.

### C. Filter / Discovery Variants

#### 15. `test_multi_select_filter` — multiple independent filters

- **Fixture**: `multi_filter/` — two `<select>` dropdowns: `category`
  (3 values) and `year` (3 values). 9 combinations, 5 items each (45
  total).
- **Tests**: Agent iterates the Cartesian product of both filters,
  collecting 45 records.
- **Pipeline stress**: Discovery Writer handles multiple filter
  dimensions; `DiscoveryAuditor` verifies all combinations visited.
- **Asserts**: `assert_min_records(45)`,
  `assert_fields_non_null(["title", "category", "year"])`.

#### 16. `test_filter_with_empty_category` — filter value with zero results

- **Fixture**: `filter_empty/` — 4 categories; one ("archived") returns
  0 items. Other 3 return 5 each (15 total).
- **Tests**: Agent iterates all 4 filter values including the empty one;
  15 records total; no crash on empty result set.
- **Pipeline stress**: Discovery Writer must handle a filter value
  that yields zero items without crashing or skipping remaining filters.
- **Asserts**: `assert_min_records(15)`.

#### 17. `test_search_filter` — search input + submit form

- **Fixture**: `search_filter/` — a text `<input>` + submit button;
  searching for "report" returns 8 items, "memo" returns 5, empty
  search returns 10 (all).
- **Tests**: Agent iterates search terms from a predefined list,
  collecting 23 records.
- **Pipeline stress**: Explorer must identify the search form as a
  filter; Discovery Writer must fill the input, submit, and iterate
  search terms.
- **Asserts**: `assert_min_records(23)`.

### D. PDF Download Edge Cases

#### 18. `test_large_pdf_download` — large PDF file (5MB+)

- **Fixture**: `large_pdf/` — 3 items linking to large PDFs (~5MB
  each). Server sends `Content-Length` header.
- **Tests**: Agent downloads all 3 large PDFs without timeout.
- **Pipeline stress**: PDF download timeout handling; `curl_cffi`
  vs `browser_fetch` strategy for large files.
- **Asserts**: `assert_pdf_count(3)`, `assert_min_records(3)`.

#### 19. `test_pdf_content_disposition` — PDF served with Content-Disposition: attachment

- **Fixture**: `pdf_content_disposition/` — 5 items; server sends
  `Content-Disposition: attachment; filename="custom_name.pdf"` header.
- **Tests**: Agent downloads 5 PDFs; filenames may differ from the URL
  basename.
- **Pipeline stress**: Processing Writer must handle
  Content-Disposition headers; downloaded filename tracking.
- **Asserts**: `assert_pdf_count(5)`.

#### 20. `test_pdf_redirect_chain` — PDF URL redirects (301/302)

- **Fixture**: `pdf_redirect/` — 5 items; `/pdf/docN.pdf` redirects to
  `/file/docN.pdf` (301). The redirect target serves the actual file.
- **Tests**: Agent follows redirects and downloads all 5 PDFs.
- **Pipeline stress**: `curl_cffi` redirect handling; `browser_fetch`
  redirect handling.
- **Asserts**: `assert_pdf_count(5)`.

#### 21. `test_pdf_requires_cookies` — PDF download needs session cookies

- **Fixture**: `pdf_cookies/` — 5 items; PDF download only succeeds if
  the request includes a session cookie set by the listing page.
- **Tests**: Agent downloads all 5 PDFs (cookie propagation from
  browser session to download request).
- **Pipeline stress**: `curl_cffi` must inherit browser cookies or
  the download strategy must fall back to `browser_fetch`.
- **Asserts**: `assert_pdf_count(5)`.

#### 22. `test_pdf_timeout` — PDF download times out (server hangs)

- **Fixture**: `pdf_timeout/` — 5 items; PDFs 1-3 download normally,
  PDFs 4-5 cause the server to hang (never respond). Server sends no
  Content-Length for hanging responses.
- **Tests**: Agent downloads 3 PDFs; handles the 2 timeouts
  gracefully (save_record with `download_status="timeout"` or
  `"failed"`); 5 records total.
- **Pipeline stress**: Download timeout handling; `save_record` with
  failure status; no script crash.
- **Asserts**: `assert_pdf_count(3)`, `assert_min_records(5)`.

### E. Resilience / Error Recovery

#### 23. `test_intermittent_500_errors` — server returns 500 on some pages

- **Fixture**: `intermittent_500/` — 5 pages; pages 2 and 4 return
  HTTP 500 on the first request, succeed on retry. 50 items total.
- **Tests**: Agent retries failed pages; collects 50 records.
- **Pipeline stress**: Navigation error recovery; retry logic in the
  emitted script; `wait_for_page_ready` handling of 500s.
- **Asserts**: `assert_min_records(50)`.

#### 24. `test_connection_reset` — server resets connection mid-response

- **Fixture**: `connection_reset/` — 10 items; every 3rd request
  gets a connection reset (RST). Retry succeeds.
- **Tests**: Agent handles connection resets; 10 records.
- **Pipeline stress**: `curl_cffi` / `browser_fetch` connection-reset
  resilience; retry logic.
- **Asserts**: `assert_min_records(10)`.

#### 25. `test_rate_limiting_429` — server returns 429 with Retry-After

- **Fixture**: `rate_limit_429/` — 3 pages, 10 items each. After
  every 2 requests, server returns 429 with `Retry-After: 1`. Retry
  after 1s succeeds.
- **Tests**: Agent respects 429 + Retry-After; 30 records.
- **Pipeline stress**: Rate-limit handling in the emitted script;
  backoff logic.
- **Asserts**: `assert_min_records(30)`.

#### 26. `test_dns_failure_retry` — first page load fails DNS, retry succeeds

- **Fixture**: `dns_failure/` — the first request to `/` returns a
  page that references a broken sub-resource (broken image/iframe from
  `nonexistent.invalid`). Content is still extractable. 10 items.
- **Tests**: Agent ignores the broken sub-resource; extracts 10 items.
- **Pipeline stress**: `wait_for_page_ready` must not hang on broken
  sub-resources; the emitted script must handle network errors for
  non-critical resources.
- **Asserts**: `assert_min_records(10)`.

#### 27. `test_stale_element_after_reload` — page reloads during extraction

- **Fixture**: `stale_element/` — 10 items; after the page loads, a
  client-side script re-renders the item list after 2s (removing and
  re-adding elements).
- **Tests**: Agent waits for the re-render to settle; extracts 10
  items with valid data (not stale references).
- **Pipeline stress**: `wait_for_anchors` / element reference
  staleness; DOM mutation handling.
- **Asserts**: `assert_min_records(10)`.

### F. Data / Field Complexity

#### 28. `test_nested_fields` — nested data in save_record

- **Fixture**: `nested_fields/` — 10 items; each has `title`,
  `metadata: {author, date, tags: [tag1, tag2]}`.
- **Tests**: Agent extracts nested fields and stores them in the
  `data` JSON of `save_record`.
- **Pipeline stress**: Processing Writer must handle nested data
  structures in `save_record`.
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title", "author", "date"])`, verify `tags`
  is a list in at least one row.

#### 29. `test_unicode_content` — non-ASCII text (accents, CJK, emoji)

- **Fixture**: `unicode_content/` — 10 items with titles containing
  accented characters (café, naïve), CJK (日本語), and emoji (📄).
- **Tests**: Agent extracts Unicode correctly; no mojibake; 10 records
  with correct title text.
- **Pipeline stress**: UTF-8 encoding throughout the pipeline; DB
  storage; JSON serialization.
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title"])`, verify at least one title
  contains a non-ASCII character.

#### 30. `test_long_text_fields` — very long titles/descriptions

- **Fixture**: `long_text/` — 5 items; each title is 500 chars, each
  description is 2000 chars.
- **Tests**: Agent extracts full long text without truncation; 5
  records.
- **Pipeline stress**: Text extraction completeness; DB storage of
  large JSON values.
- **Asserts**: `assert_min_records(5)`, verify at least one `title`
  field has length ≥ 500.

#### 31. `test_missing_fields` — some items lack optional fields

- **Fixture**: `missing_fields/` — 10 items; items 1-5 have `title`
  + `date` + `author`; items 6-10 have only `title` + `date` (no
  author span).
- **Tests**: Agent extracts all 10 records; `author` is null/empty
  for items 6-10 but present for items 1-5; no crash on missing
  elements.
- **Pipeline stress**: Processing Writer must handle missing optional
  fields gracefully (no KeyError, no crash).
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title", "date"])`, verify `author` is
  non-null in ≥5 rows and null/empty in ≥5 rows.

#### 32. `test_duplicate_urls` — same URL appears on multiple pages

- **Fixture**: `duplicate_urls/` — 3 pages; item #5 on page 1 also
  appears on page 2 (same URL). 28 unique items, 30 total links.
- **Tests**: Agent deduplicates by `source_url`; 28 unique records in
  metadata.db (not 30).
- **Pipeline stress**: `save_record` deduplication via `source_url`
  unique constraint.
- **Asserts**: `assert_min_records(28)`, verify no duplicate
  `source_url` in metadata.db.

### G. Smoke Test / Repair Loop

#### 33. `test_smoke_test_catches_syntax_error` — agent produces broken Python

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: Run the pipeline with a prompt that is deliberately
  ambiguous to increase the chance of a syntax error; verify the lint
  repair loop fixes it (or the smoke repair loop does); driver exits 0.
- **Pipeline stress**: `_lint_repair_loop` (max 1 turn); `_smoke_repair_loop`
  (max 1 turn); `_MAX_LINT_REPAIRS` and `_MAX_SMOKE_REPAIRS` constants.
- **Asserts**: `assert_driver_success`, `assert_min_records(10)`.
- **Note**: This is hard to trigger deterministically; run it as a
  regression test that the repair loops exist and don't crash even
  when not triggered.

#### 34. `test_discovery_under_collected_repair` — discovery misses items

- **Fixture**: `discovery_undercollect/` — 4 filter values, 5 items
  each (20 total). The page structure makes it easy to miss the 4th
  filter value (it's behind a "More filters" expand button).
- **Tests**: The discovery self-check detects UNDER-COLLECTED,
  triggers a repair turn, and the repaired script collects all 20
  items.
- **Pipeline stress**: `_discovery_self_check`; `under_collected_paths`
  parsing; `format_discovery_repair`; `repair_discovery`; re-emit +
  re-run.
- **Asserts**: `assert_min_records(20)`.

#### 35. `test_discovery_audit_discrepancy` — auditor finds missed filter

- **Fixture**: `audit_discrepancy/` — 3 categories, 5 items each (15
  total). The listing page also has a hidden `data-count` attribute
  on each `<option>` that the `DiscoveryAuditor` can use as an oracle.
- **Tests**: The `DiscoveryAuditor` cross-check detects a discrepancy
  (e.g., one filter value missed), triggers a repair, and the final
  script collects all 15.
- **Pipeline stress**: `DiscoveryAuditor.audit`;
  `_discovery_audit_repair`; the second repair turn
  (`_MAX_DISCOVERY_REPAIRS = 2`).
- **Asserts**: `assert_min_records(15)`.

### H. Concurrency / Parallel Runners

#### 36. `test_parallel_runners_2` — parallel_runners=2

- **Fixture**: `concurrency` (reuse existing fixture, 50 items).
- **Tests**: `run_generation_pipeline(..., parallel_runners=2)`; 50
  records, no duplicates.
- **Pipeline stress**: `parallel_runners=2` path; thread-safe
  `save_record`.
- **Asserts**: `assert_min_records(50)`, no duplicate `source_url`.

#### 37. `test_parallel_runners_8` — parallel_runners=8 (high concurrency)

- **Fixture**: `concurrency` (reuse existing fixture, 50 items).
- **Tests**: `run_generation_pipeline(..., parallel_runners=8)`; 50
  records.
- **Pipeline stress**: High-concurrency `save_record`; DB write
  contention; no deadlocks.
- **Asserts**: `assert_min_records(50)`, no duplicate `source_url`.

#### 38. `test_parallel_runners_1` — parallel_runners=1 (sequential baseline)

- **Fixture**: `concurrency` (reuse existing fixture, 50 items).
- **Tests**: `run_generation_pipeline(..., parallel_runners=1)`; 50
  records.
- **Pipeline stress**: Sequential processing baseline; no
  concurrency bugs introduced by the parallel-runners infrastructure.
- **Asserts**: `assert_min_records(50)`.

### I. Prompt / Task Variations

#### 39. `test_vague_prompt` — minimal prompt with no CSS selectors

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: Run with a deliberately vague prompt: "Go to
  http://127.0.0.1:{PORT}/?scenario=single_page_list and get all the
  document titles and dates." No CSS selectors mentioned.
- **Pipeline stress**: Explorer must discover the correct selectors
  autonomously; Processing Writer must use the Explorer-verified
  selectors, not guess.
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title", "date"])`.

#### 40. `test_overly_specific_prompt` — prompt with incorrect CSS selectors

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: Run with a prompt that specifies wrong selectors: "Use
  `div.record h2` for the title" (actual: `div.item h3 a`).
- **Pipeline stress**: Explorer must verify/correct the selectors
  during exploration, not trust the prompt's incorrect selectors.
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title", "date"])`.

#### 41. `test_prompt_with_pdf_instruction` — prompt explicitly says "download all PDFs"

- **Fixture**: `mixed_content` (reuse existing fixture, 5 PDFs).
- **Tests**: Prompt: "Extract all items and download every PDF you
  find."
- **Pipeline stress**: Explorer probes `pdf_download_strategy`;
  Processing Writer downloads PDFs using the probed strategy.
- **Asserts**: `assert_pdf_count(5)`, `assert_min_records(5)`.

#### 42. `test_prompt_requests_unrelated_fields` — prompt asks for fields not on the page

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: Prompt asks for `title`, `date`, and `isbn` (ISBN does
  not exist on the page).
- **Pipeline stress**: Explorer identifies that `isbn` is not
  available; Processing Writer sets `isbn` to null/empty without
  crashing.
- **Asserts**: `assert_min_records(10)`,
  `assert_fields_non_null(["title", "date"])`.

### J. Browser / Zendriver Specific

#### 43. `test_iframe_content` — items inside an `<iframe>`

- **Fixture**: `iframe_content/` — 10 items rendered inside an
  `<iframe src="/inner.html">`. The iframe content must be accessed
  via `frame.contentDocument` or zendriver's iframe API.
- **Tests**: Agent generates a script that switches to the iframe
  context and extracts 10 items.
- **Pipeline stress**: Explorer must explore inside iframes;
  Processing Writer must use zendriver's iframe API correctly.
- **Asserts**: `assert_min_records(10)`.

#### 44. `test_shadow_dom` — items inside a Shadow DOM

- **Fixture**: `shadow_dom/` — 10 items rendered inside a custom
  element with a Shadow DOM (`attachShadow`). Items are not
  accessible via standard CSS selectors.
- **Tests**: Agent generates a script that pierces the Shadow DOM
  (using `::shadow`, `>>>`, or `shadowRoot.querySelector`).
- **Pipeline stress**: Explorer must discover Shadow DOM content;
  Processing Writer must use shadow-piercing selectors.
- **Asserts**: `assert_min_records(10)`.

#### 45. `test_lazy_loaded_images` — images with `loading="lazy"`

- **Fixture**: `lazy_images/` — 10 items; each has an `<img>` with
  `loading="lazy"` and `data-src`. Images only load when scrolled
  into view.
- **Tests**: Agent extracts 10 records; no crash from unloaded
  images (the `src` attribute is empty until scrolled).
- **Pipeline stress**: `wait_for_page_ready` / image loading; the
  emitted script must not depend on `img.src` being populated.
- **Asserts**: `assert_min_records(10)`.

#### 46. `test_new_tab_links` — links with `target="_blank"`

- **Fixture**: `new_tab/` — 10 items; each link has
  `target="_blank"`. Clicking opens a new tab.
- **Tests**: Agent generates a script that handles `target="_blank"`
  links (either by removing the attribute, intercepting the new
  tab, or using `href` directly).
- **Pipeline stress**: `browser.tabs` handling; `target="_blank"`
  mitigation in the emitted script.
- **Asserts**: `assert_min_records(10)`.

### K. Edge Cases / Boundary Conditions

#### 47. `test_empty_page` — page with zero items

- **Fixture**: `empty_page/` — a valid page with a `.item-list`
  container but zero `.item` elements.
- **Tests**: Agent generates a script that handles zero items
  gracefully; 0 records; driver exits 0 (not a crash).
- **Pipeline stress**: Processing Writer must not crash on empty
  result set; `save_record` is never called but the script exits
  cleanly.
- **Asserts**: `assert_driver_success`, `record_count == 0`.

#### 48. `test_single_item` — page with exactly 1 item

- **Fixture**: `single_item/` — 1 item on a single page.
- **Tests**: Agent extracts 1 record; no off-by-one in pagination
  logic.
- **Pipeline stress**: Boundary condition; pagination loop must not
  execute (or execute once and terminate).
- **Asserts**: `assert_min_records(1)`.

#### 49. `test_deeply_nested_pagination` — 20 pages of pagination

- **Fixture**: `deep_pagination/` — 20 pages, 5 items each (100
  total). Next button on every page except the last.
- **Tests**: Agent paginates through all 20 pages; 100 records.
- **Pipeline stress**: Long pagination loop; no memory leak; no
  early termination.
- **Asserts**: `assert_min_records(100)`.

#### 50. `test_mixed_pagination_and_filter` — filter + pagination combined

- **Fixture**: `filter_pagination/` — 2 categories, 3 pages each,
  5 items per page (30 total). Filter changes reset to page 1.
- **Tests**: Agent iterates both filters, paginates within each, and
  collects 30 records.
- **Pipeline stress**: Discovery Writer must handle filter + pagination
  nesting; the audit must verify all filter×page combinations.
- **Asserts**: `assert_min_records(30)`.

### L. Artifacts / Cleanup

#### 51. `test_artifact_cleanup` — intermediate files removed after generation

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: After the driver completes, verify that `.raw.py`,
  `.json` sidecar, and earlier `.py` versions are removed; only the
  final `.py` of each kind remains.
- **Pipeline stress**: `_cleanup_emit_artifacts`; the keepers set
  logic; `by_kind` classification by `__discover__` in filename.
- **Asserts**: `assert_driver_success`; glob `run_path/scripts/*.raw.py`
  returns 0 files; glob `run_path/scripts/*.json` returns 0 files;
  at most 1 processing `.py` and 1 discovery `.py` remain.

#### 52. `test_task_split_json_persisted` — `task_split.json` is written

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: After the driver completes, verify `run_path/task_split.json`
  exists and contains valid JSON with `needs_discovery`,
  `discovery_prompt`, `processing_prompt`, and `sample_document_urls`.
- **Pipeline stress**: `_persist_split`; `TaskSplit.model_dump`.
- **Asserts**: `assert_driver_success`; `task_split.json` exists;
  JSON parses; has all required fields.

#### 53. `test_prior_feedback_integration` — prior report feedback is used

- **Fixture**: `single_page_list` (reuse existing fixture).
- **Tests**: Write a fake prior report to `run_path/reports/report.md`
  before running the driver; verify the driver does not crash and
  the context includes the prior feedback.
- **Pipeline stress**: `PriorReportReader.read`; context merging
  logic (`prior_feedback + concurrency`).
- **Asserts**: `assert_driver_success`, `assert_min_records(10)`.
- **Note**: Hard to verify the feedback was *used* by the agent; this
  test verifies the plumbing doesn't crash.

---

## Summary

| # | Test name | New fixture? | Pipeline dimension stressed |
|---|---|---|---|
| 1 | `test_spa_dynamic_rendering` | No | JS rendering, wait_for_page_ready |
| 2 | `test_infinite_scroll_load_more` | No | AJAX load-more, button click loop |
| 3 | `test_dropdown_filter_iteration` | No | Filter iteration, DiscoveryAuditor |
| 4 | `test_concurrency_parallel_runners` | No | parallel_runners=4 |
| 5 | `test_pdf_download_modal` | No | PDF download strategy |
| 6 | `test_mixed_content` | No | PDF vs HTML distinction |
| 7 | `test_no_discovery_needed` | Yes | needs_discovery=false |
| 8 | `test_discovery_needed_pagination` | Yes | needs_discovery=true |
| 9 | `test_detail_page_extraction` | Yes | List → detail navigation |
| 10 | `test_numbered_pagination` | Yes | Numbered page links |
| 11 | `test_cursor_pagination` | Yes | Cursor/offset pagination |
| 12 | `test_infinite_scroll_no_button` | Yes | Scroll-to-load |
| 13 | `test_pagination_disabled_next` | Yes | Disabled Next button |
| 14 | `test_pagination_relative_urls` | Yes | Relative URL construction |
| 15 | `test_multi_select_filter` | Yes | Multiple filter dimensions |
| 16 | `test_filter_with_empty_category` | Yes | Empty filter result |
| 17 | `test_search_filter` | Yes | Search form as filter |
| 18 | `test_large_pdf_download` | Yes | Large PDF timeout |
| 19 | `test_pdf_content_disposition` | Yes | Content-Disposition header |
| 20 | `test_pdf_redirect_chain` | Yes | Redirect following |
| 21 | `test_pdf_requires_cookies` | Yes | Cookie propagation |
| 22 | `test_pdf_timeout` | Yes | Download timeout handling |
| 23 | `test_intermittent_500_errors` | Yes | 500 retry |
| 24 | `test_connection_reset` | Yes | Connection reset |
| 25 | `test_rate_limiting_429` | Yes | 429 + Retry-After |
| 26 | `test_dns_failure_retry` | Yes | Broken sub-resource |
| 27 | `test_stale_element_after_reload` | Yes | DOM mutation / staleness |
| 28 | `test_nested_fields` | Yes | Nested data structures |
| 29 | `test_unicode_content` | Yes | UTF-8 / non-ASCII |
| 30 | `test_long_text_fields` | Yes | Large text extraction |
| 31 | `test_missing_fields` | Yes | Optional field absence |
| 32 | `test_duplicate_urls` | Yes | source_url deduplication |
| 33 | `test_smoke_test_catches_syntax_error` | No | Lint + smoke repair loop |
| 34 | `test_discovery_under_collected_repair` | Yes | UNDER-COLLECTED self-check |
| 35 | `test_discovery_audit_discrepancy` | Yes | DiscoveryAuditor repair |
| 36 | `test_parallel_runners_2` | No | parallel_runners=2 |
| 37 | `test_parallel_runners_8` | No | parallel_runners=8 |
| 38 | `test_parallel_runners_1` | No | parallel_runners=1 |
| 39 | `test_vague_prompt` | No | Autonomous selector discovery |
| 40 | `test_overly_specific_prompt` | No | Selector correction |
| 41 | `test_prompt_with_pdf_instruction` | No | PDF strategy probing |
| 42 | `test_prompt_requests_unrelated_fields` | No | Missing field handling |
| 43 | `test_iframe_content` | Yes | Iframe context |
| 44 | `test_shadow_dom` | Yes | Shadow DOM piercing |
| 45 | `test_lazy_loaded_images` | Yes | Lazy image loading |
| 46 | `test_new_tab_links` | Yes | target="_blank" |
| 47 | `test_empty_page` | Yes | Zero items |
| 48 | `test_single_item` | Yes | Single item boundary |
| 49 | `test_deeply_nested_pagination` | Yes | 20-page pagination |
| 50 | `test_mixed_pagination_and_filter` | Yes | Filter + pagination |
| 51 | `test_artifact_cleanup` | No | _cleanup_emit_artifacts |
| 52 | `test_task_split_json_persisted` | No | _persist_split |
| 53 | `test_prior_feedback_integration` | No | PriorReportReader |

**Total: 53 tests** (6 quick wins from existing fixtures, 34 new
fixtures + tests, 13 reuse existing fixtures).

## Implementation Notes

- All tests follow the existing pattern in `tests/`: import from
  `tests.conftest`, define a module-level `PROMPT` constant with
  `{PORT}` placeholder, call `run_generation_pipeline`, assert with
  `assert_driver_success` / `assert_min_records` / `assert_pdf_count`
  / `assert_fields_non_null`.
- New fixtures go under `scripts/fixtures/<scenario_name>/` with a
  `_dynamic.py` (dynamic) or `index.html` (static) following the
  existing `fixture_server.py` routing contract:
  - `index(query)` → rendered HTML for the listing page.
  - `fragment(query)` → AJAX fragment HTML (for scroll/load-more).
  - `custom_route(path, query)` → `(body, mime, status)` for
    detail pages, PDF routes, 404/403/503/429 responses.
- The fixture server (`scripts/fixture_server.py`) already supports
  custom status codes (403, 503) via `custom_route` return value; 429
  and 500 may need a small addition to `_route_with_status` if not
  already handled (check the `else` branch — it calls `_send_ok`).
- Per `AGENTS.md`: no `__init__.py`, no `__all__`, constants in the
  script not arguments, pydantic models in `domain/` (not needed for
  tests/fixtures).
- Tests are slow (minutes each) by design — they run the full LLM
  pipeline. Consider marking them with `@pytest.mark.slow` or a
  custom marker for selective execution.