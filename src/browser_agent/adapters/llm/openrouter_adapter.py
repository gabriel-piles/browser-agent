"""LlmPort backed by OpenRouter's OpenAI-compatible endpoint."""

from __future__ import annotations

import os

from pydantic_ai.models import Model

from browser_agent.adapters.llm.openai_compatible_chain import build_fallback_chain
from browser_agent.configuration import MODEL_PRIMARY
from browser_agent.ports.llm_port import LlmPort


_BASE_URL = "https://openrouter.ai/api/v1"
_API_KEY_ENV = "OPENROUTER_API_KEY"


class OpenRouterAdapter(LlmPort):
    """An :class:`LlmPort` backed by OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        self.model_name = MODEL_PRIMARY
        self.api_key = os.environ.get(_API_KEY_ENV)
        if not self.api_key:
            raise RuntimeError(f"{_API_KEY_ENV} must be set in the environment or .env file")

    def get_model(self) -> Model:
        return build_fallback_chain(self.api_key, _BASE_URL)
