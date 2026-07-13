import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
# LLM connection — consumed by ``adapters/llm/ollama_adapter.py``.
OLLAMA_BASE_URL = "https://ollama.com/v1"
ORCHESTRATOR_MODEL = "deepseek-v4-flash:cloud"

# Where the generated scripts are persisted by the driver.
SCRIPTS_PATH = PROJECT_ROOT / "data" / "scripts"
METADATA_DB_PATH = PROJECT_ROOT / "data" / "metadata.db"
PROMPT_FILE = PROJECT_ROOT / "data" / "prompt.txt"

MAX_LLM_CALLS = 25
MAX_VALIDATION_ATTEMPTS = 3

SCRIPTS_PATH.mkdir(parents=True, exist_ok=True)
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
