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
ORCHESTRATOR_MODEL = "deepseek-v4-pro:cloud"
VERIFICATION_MODEL = "minimax-m3:cloud"
VERIFICATION_PDF_COUNT = 10
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
UWAZI_PUSH_MAX_WORKERS = 8

MAX_LLM_CALLS = 50
SNAPSHOT_MAX_CHARS = 50_000
COMPACT_KEEP_RECENT_VALIDATIONS = 1
COMPACT_TRUNCATED_PLACEHOLDER = "[trimmed — see latest snapshot]"
COMPACT_MIN_TRIM_CHARS = 1_000
COMPACT_HEAD_LINES = 6
COMPACT_MAX_EXTRACTED_LINES = 10

# --- Analysis action limits ---
# Maximum elements per category returned by ``explore_page(action='analyze')``
ANALYZE_MAX_LINKS = 50
ANALYZE_MAX_BUTTONS = 15
ANALYZE_MAX_INPUTS = 15
ANALYZE_MAX_HEADINGS = 20
ANALYZE_MAX_TABLES = 5

# --- Compactor — structured-analysis tuning ---
# Keep more recent structured/summary returns full (they're small)
COMPACT_KEEP_RECENT_STRUCTURED = 5
# Max chars for structured-analysis content before compactor considers trimming
COMPACT_STRUCTURED_MAX_TRIM_CHARS = 3_000
MAX_VALIDATION_ATTEMPTS = 3

# ``headless`` defaults to False — the operator can watch Chrome
# navigate during inspection and the generated script. Set the env
# var to ``1`` / ``true`` for headless runs.
ZENDRIVER_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
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
