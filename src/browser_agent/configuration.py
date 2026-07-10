import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
# LLM connection — consumed by ``adapters/llm/ollama_adapter.py``.
OLLAMA_BASE_URL = "https://ollama.com/v1"
ORCHESTRATOR_MODEL = "deepseek-v4-flash:cloud"

# Where the generated scripts are persisted by the driver.
SCRIPTS_PATH = Path(
    os.environ.get(
        "SCRIPTS_PATH",
        str(Path(__file__).parent.parent.parent / "data" / "scripts"),
    )
)
SCRIPTS_PATH.mkdir(parents=True, exist_ok=True)

# Where the inspection tool's zendriver session writes its download
# artifacts. Operators can override the location with the env var.
ZENDRIVER_DOWNLOADS_DIR = Path(
    os.environ.get(
        "ZENDRIVER_DOWNLOADS_DIR",
        str(Path(__file__).parent.parent.parent / "data" / "downloads" / "zendriver"),
    )
)
ZENDRIVER_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ``headless`` defaults to False — the operator can watch Chrome
# navigate during inspection and the generated script. Set the env
# var to ``1`` / ``true`` for headless runs.
ZENDRIVER_HEADLESS = os.environ.get("ZENDRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}

# Hard probe timeout (seconds). The inspection tool bails out and
# returns a truncated snippet if Chrome doesn't navigate inside this
# window. Useful when the target is gated.
ZENDRIVER_PROBE_TIMEOUT_S = float(os.environ.get("ZENDRIVER_PROBE_TIMEOUT_S", "30"))
