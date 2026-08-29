"""LlmPort backed by OpenCode Zen's OpenAI-compatible endpoint."""

from __future__ import annotations

import os

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.providers.openai import OpenAIProvider

from browser_agent.adapters.llm.retrying_chat_model import RetryingChatModel
from browser_agent.configuration import LLM_MAX_RETRIES, LLM_REQUEST_TIMEOUT_S, MODEL
from browser_agent.ports.llm_port import LlmPort

_BASE_URL = "https://opencode.ai/zen/v1"
_API_KEY_ENV = "OPENCODE_ZEN_API_KEY"


class OpenCodeZenAdapter(LlmPort):
    """An :class:`LlmPort` backed by OpenCode Zen's OpenAI-compatible endpoint."""

    def __init__(self) -> None:
        self.model_name = MODEL
        self.api_key = os.environ.get(_API_KEY_ENV)
        if not self.api_key:
            raise RuntimeError(f"{_API_KEY_ENV} must be set in the environment or .env file")

    def get_model(self) -> Model:
        # max_retries=0: the app-layer RetryingChatModel already retries
        # LLM_MAX_RETRIES times on transient errors, so leaving the transport
        # layer at its default would compound into LLM_MAX_RETRIES**2 attempts
        # and let a stalled socket hang for ~LLM_MAX_RETRIES*600s. The explicit
        # timeout (LLM_REQUEST_TIMEOUT_S) fails a dead connection fast so the
        # run recovers instead of freezing.
        client = AsyncOpenAI(
            base_url=_BASE_URL,
            api_key=self.api_key,
            max_retries=0,
            timeout=LLM_REQUEST_TIMEOUT_S,
        )
        provider = OpenAIProvider(openai_client=client)
        return RetryingChatModel(self.model_name, provider=provider)
