from __future__ import annotations

import os

from pydantic_ai.models import Model
from pydantic_ai.providers.ollama import OllamaProvider

from browser_agent.configuration import OLLAMA_BASE_URL, ORCHESTRATOR_MODEL
from browser_agent.ports.llm_port import LlmPort


class OllamaAdapter(LlmPort):
    """An :class:`LlmPort` backed by an OpenAI-compatible Ollama endpoint."""

    def __init__(
        self,
        model: str | None = None,
        ollama_base_url: str | None = None,
        ollama_api_key: str | None = None,
    ) -> None:
        self.model_name = model or ORCHESTRATOR_MODEL
        self.ollama_base_url = ollama_base_url or OLLAMA_BASE_URL
        self.ollama_api_key = ollama_api_key or os.environ.get("OLLAMA_API_KEY")
        if not self.ollama_api_key:
            raise RuntimeError("OLLAMA_API_KEY must be set in the environment or .env file")

    def get_model(self) -> Model:
        provider = OllamaProvider(base_url=self.ollama_base_url, api_key=self.ollama_api_key)
        # pydantic-ai accepts any OpenAI-compatible endpoint via
        # ``OpenAIChatModel``; OllamaProvider wires the base URL and
        # auth header for us.
        from pydantic_ai.models.openai import OpenAIChatModel
        return OpenAIChatModel(self.model_name, provider=provider)
