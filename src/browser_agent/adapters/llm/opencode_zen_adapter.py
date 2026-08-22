from __future__ import annotations

import asyncio
import os

from loguru import logger
from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from browser_agent.configuration import (
    OPENCODE_ZEN_BASE_URL,
    OPENCODE_ZEN_MAX_RETRIES,
    OPENCODE_ZEN_MODEL,
)
from browser_agent.ports.llm_port import LlmPort

_TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_RETRY_BASE_DELAY_S = 5.0
_RETRY_MAX_DELAY_S = 60.0


class _RetryingChatModel(OpenAIChatModel):
    """Retry transient provider errors with long backoff.

    The shared Zen endpoint has multi-minute outage windows; the OpenAI
    SDK's built-in retries (~25s ceiling) are not enough and a single
    unhandled :class:`ModelHTTPError` aborts a whole agent run that may
    already have spent many minutes of work.
    """

    async def request(self, messages, model_settings, model_request_parameters):  # noqa: D102
        for attempt in range(OPENCODE_ZEN_MAX_RETRIES):
            try:
                return await super().request(messages, model_settings, model_request_parameters)
            except ModelHTTPError as exc:
                retryable = exc.status_code in _TRANSIENT_STATUS and attempt < OPENCODE_ZEN_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM {} returned HTTP {} — retry {}/{} in {:.0f}s",
                    self.model_name,
                    exc.status_code,
                    attempt + 1,
                    OPENCODE_ZEN_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except ModelAPIError as exc:
                retryable = attempt < OPENCODE_ZEN_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM {} connection error ({}) — retry {}/{} in {:.0f}s",
                    self.model_name,
                    exc.message,
                    attempt + 1,
                    OPENCODE_ZEN_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):  # noqa: D102
        for attempt in range(OPENCODE_ZEN_MAX_RETRIES):
            try:
                return await super().request_stream(messages, model_settings, model_request_parameters, run_context)
            except ModelHTTPError as exc:
                retryable = exc.status_code in _TRANSIENT_STATUS and attempt < OPENCODE_ZEN_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM {} returned HTTP {} (stream) — retry {}/{} in {:.0f}s",
                    self.model_name,
                    exc.status_code,
                    attempt + 1,
                    OPENCODE_ZEN_MAX_RETRIES,
                    delay,
                )
            except ModelAPIError as exc:
                retryable = attempt < OPENCODE_ZEN_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM {} connection error (stream) ({}) — retry {}/{} in {:.0f}s",
                    self.model_name,
                    exc.message,
                    attempt + 1,
                    OPENCODE_ZEN_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)


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
        client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            max_retries=OPENCODE_ZEN_MAX_RETRIES,
        )
        provider = OpenAIProvider(openai_client=client)
        return _RetryingChatModel(self.model_name, provider=provider)
