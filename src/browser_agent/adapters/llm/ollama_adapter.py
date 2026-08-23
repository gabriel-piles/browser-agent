"""LlmPort backed by an OpenAI-compatible Ollama endpoint."""

from __future__ import annotations

import os

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

from browser_agent.configuration import MODEL
from browser_agent.ports.llm_port import LlmPort

_BASE_URL = "https://ollama.com/v1"
_API_KEY_ENV = "OLLAMA_API_KEY"
# The ollama.com cloud endpoint routes by a suffixed catalog id; remap the
# shared configuration ``MODEL`` onto its ollama-specific name when needed.
_MODEL_IDS = {"deepseek-v4-flash": "deepseek-v4-flash:0731-cloud"}


class OllamaAdapter(LlmPort):
    """An :class:`LlmPort` backed by an OpenAI-compatible Ollama endpoint."""

    def __init__(self) -> None:
        self.model_name = _MODEL_IDS.get(MODEL, MODEL)
        self.api_key = os.environ.get(_API_KEY_ENV)
        if not self.api_key:
            raise RuntimeError(f"{_API_KEY_ENV} must be set in the environment or .env file")

    def get_model(self) -> Model:
        # OllamaProvider wires the base URL and auth header for us; any
        # OpenAI-compatible endpoint works through ``OpenAIChatModel``.
        provider = OllamaProvider(base_url=_BASE_URL, api_key=self.api_key)
        return OpenAIChatModel(self.model_name, provider=provider)
