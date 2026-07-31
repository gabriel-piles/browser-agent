from __future__ import annotations

import os

from pydantic_ai.models import Model
from pydantic_ai.providers.openai import OpenAIProvider

from browser_agent.configuration import OPENCODE_ZEN_BASE_URL, OPENCODE_ZEN_MODEL
from browser_agent.ports.llm_port import LlmPort


class OpenCodeZenAdapter(LlmPort):
    """An :class:`LlmPort` backed by OpenCode Zen's OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model_name = model or OPENCODE_ZEN_MODEL
        self.base_url = base_url or OPENCODE_ZEN_BASE_URL
        self.api_key = api_key or os.environ.get("OPENCODE_ZEN_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENCODE_ZEN_API_KEY must be set in the environment or .env file")

    def get_model(self) -> Model:
        provider = OpenAIProvider(base_url=self.base_url, api_key=self.api_key)
        # pydantic-ai accepts any OpenAI-compatible endpoint via
        # ``OpenAIChatModel``; OpenAIProvider wires the base URL and
        # auth header for us.
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel(self.model_name, provider=provider)
