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
ORCHESTRATOR_MODEL = "kimi-k2.7-code:cloud"

# YAML file that defines every run: ``active_run`` (the name to
# execute) plus a list of ``runs`` each with ``name`` and ``prompt``.
RUNS_FILE = PROJECT_ROOT / "data" / "runs.yaml"
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

MAX_LLM_CALLS = 25
MAX_VALIDATION_ATTEMPTS = 3

# ``headless`` defaults to False — the operator can watch Chrome
# navigate during inspection and the generated script. Set the env
# var to ``1`` / ``true`` for headless runs.
ZENDRIVER_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}

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
