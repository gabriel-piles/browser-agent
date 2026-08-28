"""LlmPort backed by OpenRouter's OpenAI-compatible endpoint."""

from __future__ import annotations

import os

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.providers.openai import OpenAIProvider

from browser_agent.adapters.llm.retrying_chat_model import RetryingChatModel
from browser_agent.configuration import LLM_MAX_RETRIES, MODEL
from browser_agent.ports.llm_port import LlmPort

_BASE_URL = "https://openrouter.ai/api/v1"
_API_KEY_ENV = "OPENROUTER_API_KEY"
_REQUEST_TIMEOUT_S = 600.0


class OpenRouterAdapter(LlmPort):
    """An :class:`LlmPort` backed by OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        self.model_name = MODEL
        self.api_key = os.environ.get(_API_KEY_ENV)
        if not self.api_key:
            raise RuntimeError(f"{_API_KEY_ENV} must be set in the environment or .env file")

    def get_model(self) -> Model:
        client = AsyncOpenAI(
            base_url=_BASE_URL, api_key=self.api_key, max_retries=LLM_MAX_RETRIES, timeout=_REQUEST_TIMEOUT_S
        )
        provider = OpenAIProvider(openai_client=client)
        return RetryingChatModel(self.model_name, provider=provider)
