import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Load ``.env`` from the project root once at import time so every
# ``os.environ.get(...)`` below (and the ``OllamaAdapter``) sees the
# operator-provided secrets. ``python-dotenv`` is already a runtime
# dependency. Existing shell env vars win by default (``override=False``).
load_dotenv(PROJECT_ROOT / ".env")

# LLM connection — consumed by ``adapters/llm/ollama_adapter.py``.
OLLAMA_BASE_URL = "https://ollama.com/v1"
ORCHESTRATOR_MODEL = "deepseek-v4-flash:0731-cloud"
MAX_LLM_CALLS = 70
EXPLORER_MAX_LLM_CALLS = 30
WRITER_MAX_LLM_CALLS = 40
ORCHESTRATOR_MAX_LLM_CALLS = 15
# Output token budget sent to the provider on every LLM request. Without an
# explicit `max_tokens`, reasoning models (deepseek-v4-flash) can spend the
# provider's default budget entirely on thinking and return `finish_reason='length'`
# with no actionable output, which pydantic-ai treats as a fatal error. 96k
# gives the reasoning model enough room for a long thinking pass plus a
# structured result on the most complex scenarios (discovery with
# expand-button repair, multi-filter iteration); the model's 128k context
# leaves 32k for input (system prompt + compacted tool returns), which is
# ample since the compactor bounds prompt size to COMPACT_INPUT_TOKEN_BUDGET.
MAX_OUTPUT_TOKENS = 96_000
VERIFICATION_MODEL = "deepseek-v4-flash:0731-cloud"
VERIFICATION_PDF_COUNT = 10

# LLM connection — consumed by ``adapters/llm/opencode_zen_adapter.py``.
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_ZEN_MODEL = "deepseek-v4-flash"
VERIFICATION_SCRIPT_RUN_LIMIT = 15
VERIFICATION_QUERY_LIMIT = 10

# YAML file that defines the active run: only ``active_run`` (the name of a
# YAML in ``data/prompts/``, with or without the ``.yaml`` suffix). The prompt
# config itself lives in ``data/prompts/<active_run>.yaml`` and is copied
# into the run folder as ``run.yaml`` at execution time for a historical
# snapshot.
RUNS_FILE = PROJECT_ROOT / "data" / "active_run.yaml"
# Source-of-truth prompt YAMLs, keyed by run name (filename stem).
PROMPTS_PATH = PROJECT_ROOT / "data" / "prompts"
# Per-run root: scripts, downloads and metadata.db all live under
# ``data/runs/<active_run>/``.
RUNS_PATH = PROJECT_ROOT / "data" / "runs"
# Mappings and thesaurus-mappings for the three Uwazi drivers
# (``uwazi_propose``, ``uwazi_match``, ``uwazi_apply``) are stored
# alongside the rest of the per-run artifacts so each run keeps its
# own draft + reviewed set of mapping YAMLs.
MAPPINGS_DIRNAME = "mappings"
THESAURI_MAPPINGS_DIRNAME = "thesauri_mappings"

# Uwazi HTTP API connection — consumed by the three Uwazi drivers.
# Operators set these in the project .env file; defaults exist for
# the local dev stack but the drivers refuse to start without a
# real ``UWAZI_URL`` so a missing config fails fast.
UWAZI_URL = os.environ.get("UWAZI_URL", "http://localhost:3000")
UWAZI_USER = os.environ.get("UWAZI_USER", "admin")
UWAZI_PASSWORD = os.environ.get("UWAZI_PASSWORD", "admin")
# Default language code sent to Uwazi when a mapping does not pin one.
UWAZI_DEFAULT_LANGUAGE = os.environ.get("UWAZI_DEFAULT_LANGUAGE", "en")
# Max worker threads for concurrent Uwazi entity push.
UWAZI_PUSH_MAX_WORKERS = 1
# Target max prompt tokens sent to the model by the compactor. The
# model (deepseek-v4-flash:0731-cloud) has a 128k-token context window;
# MAX_OUTPUT_TOKENS is 96k, leaving 32k for input. The compactor budget
# is set to 28k (4k safety margin) so input + output never exceeds 128k.
# The compactor trims aggressively when this budget is exceeded, keeping
# recent tool returns and dropping old ones.
COMPACT_INPUT_TOKEN_BUDGET = 28_000
# Cumulative input-token ceiling on ``UsageLimits`` across the whole
# run (pydantic-ai sums input tokens across every request, so this is
# NOT a per-prompt cap — each prompt is bounded to COMPACT_INPUT_TOKEN_BUDGET
# by the compactor). Set far above 40 × 300k so it never trips a normal
# exploration; it only guards against a runaway agent looping forever.
# If it fires on a legitimate run, raise it further.
AGENT_INPUT_TOKEN_LIMIT = 15_000_000

SNAPSHOT_MAX_CHARS = 50_000
# Max link lines in the "# Page links" section of an untrimmed explore return.
SNAPSHOT_LINK_LINES = 60
# Max lines kept per link-bearing section when the compactor trims a return.
COMPACT_KEEP_LINK_LINES = 30
# Max lines kept per section by the aggressive (second-tier) trim.
COMPACT_HARD_KEEP_LINES = 6
COMPACT_KEEP_RECENT_VALIDATIONS = 1
COMPACT_TRUNCATED_PLACEHOLDER = "[trimmed — see latest snapshot]"
COMPACT_MIN_TRIM_CHARS = 1_000
COMPACT_HEAD_LINES = 6

# --- Analysis action limits ---
# Maximum elements per category returned by ``explore_page(action='analyze')``
ANALYZE_MAX_LINKS = 50
ANALYZE_MAX_BUTTONS = 15
ANALYZE_MAX_INPUTS = 15
ANALYZE_MAX_HEADINGS = 20
ANALYZE_MAX_TABLES = 5

# --- Compactor — structured-analysis tuning ---
# Max element lines per section kept in trimmed ``analyze`` returns;
# higher than ``COMPACT_MAX_EXTRACTED_LINES`` because link/button
# sections need more visible entries for correct selector choice.
COMPACT_MAX_ANALYZE_LINES = 20
MAX_VALIDATION_ATTEMPTS = 3
# Hard cap on explore_page calls per agent turn. When reached, the tool
# refuses and directs the agent to emit. Guards against the agent looping
# on a dead selector and inflating context until the LLM request times out.
MAX_EXPLORE_CALLS = 30
# Explore-call budget for the dedicated discovery-completeness verifier:
# ~23 manifest targets × (navigate + repeated infinite-scroll) exceeds the
# generic MAX_EXPLORE_CALLS budget above, so only the discovery branch runs
# with this raised limit.
DISCOVERY_VERIFICATION_EXPLORE_LIMIT = 60
# Consecutive empty explore_page results (extract returning 0 elements, or
# inspect erroring with "no element matches") before the tool refuses and
# directs the agent to emit or run analyze.
MAX_EMPTY_EXPLORE_RESULTS = 3

# ``headless`` defaults to False — the operator can watch Chrome
# navigate during inspection and the generated script. Set the env
# var to ``1`` / ``true`` for headless runs.
ZENDRIVER_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
# Optional Chromium window placement, e.g. "2560,0" to open windows on a
# secondary monitor. Set in the project .env. Empty = no placement flag.
CHROMIUM_WINDOW_POSITION = os.environ.get("CHROMIUM_WINDOW_POSITION", "")
# NopeCHA CAPTCHA-solver extension. Opt-in: the extension only loads when
# ``NOPECHA_ENABLED`` is truthy. ``NOPECHA_KEY`` is optional — the free tier
# (100 solves/day, keyed by IP) needs no key; a paid key raises the limit.
# When enabled, the automation build is downloaded once, unzipped to a
# cached directory under the project, its manifest.json is patched with the
# key + enabled CAPTCHA types, and the dir is passed to Chromium via
# ``--load-extension``. Leave unset to keep the stealth-clean launch with
# zero extension flags.
NOPECHA_ENABLED = os.environ.get("NOPECHA_ENABLED", "false").lower() in {"1", "true", "yes"}
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "")
NOPECHA_VERSION = "0.6.1"  # pin; bump manually when upgrading
NOPECHA_DOWNLOAD_URL = (
    f"https://github.com/NopeCHALLC/nopecha-extension/releases/download/{NOPECHA_VERSION}/chromium_automation.zip"
)
NOPECHA_CACHE_DIR = PROJECT_ROOT / "data" / "nopecha-extension"
NOPECHA_SOLVE_TIMEOUT_S = float(os.environ.get("NOPECHA_SOLVE_TIMEOUT_S", "30"))

# Hard probe timeout (seconds). The inspection tool bails out and
# returns a truncated snippet if Chrome doesn't navigate inside this
# window. Useful when the target is gated.
ZENDRIVER_PROBE_TIMEOUT_S = float(os.environ.get("ZENDRIVER_PROBE_TIMEOUT_S", "30"))

# Page loading and anchor-stability timing — mirrors the strategy used in
# the scrape-to-uwazi project so zendriver waits for real CDP frame events
# and network idle instead of a fixed sleep.
PAGE_LOAD_TIMEOUT_SECONDS = 45.0
PAGE_LOAD_WAIT_UNTIL = "networkidle"  # "load" or "networkidle"
PAGE_LOAD_NETWORK_QUIET_WINDOW_MS = 500
ANCHOR_STABILITY_MIN_WAIT_SECONDS = 3.0
ANCHOR_STABILITY_MAX_WAIT_SECONDS = 8.0
ANCHOR_STABILITY_POLL_INTERVAL_SECONDS = 0.2
ANCHOR_STABILITY_REQUIRED_STABLE_POLLS = 2

# Browser lifecycle timeouts to prevent zendriver from hanging
BROWSER_TAB_OPEN_TIMEOUT_SECONDS = 45.0
BROWSER_TAB_LOAD_TIMEOUT_SECONDS = 20.0

# Chromium's setuid sandbox fails when running as root (e.g. in containers).
# Auto-enable --no-sandbox when uid is 0; override with CHROMIUM_NO_SANDBOX env.
CHROMIUM_NO_SANDBOX = os.environ.get("CHROMIUM_NO_SANDBOX", "").lower() in {"1", "true", "yes"} or os.geteuid() == 0
