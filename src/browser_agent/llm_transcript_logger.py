"""Run-scoped, thread-safe sink for per-agent LLM transcripts.

Each completed agent run writes one JSON file under the configured
directory, capturing the prompt, the full message history (tool calls,
tool results, model responses) and the real pydantic-ai usage. A coding
agent pointed at a run's ``debug/`` folder can reconstruct exactly what
every agent saw and reasoned over, without re-running the flow.
"""

from __future__ import annotations

from datetime import date, datetime, time

import json
import os
import threading
from pathlib import Path

from pydantic_ai.messages import ModelMessagesTypeAdapter

_LOCK = threading.Lock()
_DIR: Path | None = None
_SEQ = 0


def configure_llm_transcript_dir(path: Path) -> None:
    """Point the sink at ``path`` and reset the per-run sequence counter."""
    global _DIR, _SEQ
    path.mkdir(parents=True, exist_ok=True)
    _DIR = path
    _SEQ = 0


def _json_default(obj):
    """Serialize values the pydantic-ai message dump leaves as rich objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (date, time)):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def write_llm_transcript(agent_name: str, prompt: str, messages: list, usage: dict[str, int]) -> None:
    """Persist one agent run as a JSON transcript; no-op when not configured."""
    global _SEQ
    if _DIR is None:
        return
    payload = {
        "agent": agent_name,
        "prompt": prompt,
        "messages": ModelMessagesTypeAdapter.dump_python(messages),
        "usage": usage,
    }
    with _LOCK:
        _SEQ += 1
        text = json.dumps(payload, ensure_ascii=False, default=_json_default)
        _atomic_write(_DIR / f"{_SEQ:06d}_{agent_name}.json", text)


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` (crash-safe)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
