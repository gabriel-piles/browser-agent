"""OpenAI-compatible chat model that retries transient provider errors."""

from __future__ import annotations

import asyncio

from loguru import logger
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models.openai import OpenAIChatModel

from browser_agent.configuration import LLM_MAX_RETRIES

_TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_RETRY_BASE_DELAY_S = 5.0
_RETRY_MAX_DELAY_S = 60.0


class RetryingChatModel(OpenAIChatModel):
    """Retry transient provider errors with long backoff.

    Shared endpoints have multi-minute outage windows; the OpenAI SDK's
    built-in retries (~25s ceiling) are not enough and a single unhandled
    :class:`ModelHTTPError` aborts a whole agent run that may already have
    spent many minutes of work.
    """

    async def request(self, messages, model_settings, model_request_parameters):  # noqa: D102
        for attempt in range(LLM_MAX_RETRIES):
            try:
                return await super().request(messages, model_settings, model_request_parameters)
            except ModelHTTPError as exc:
                retryable = exc.status_code in _TRANSIENT_STATUS and attempt < LLM_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM HTTP {} — retry {}/{} in {:.0f}s",
                    exc.status_code,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except ModelAPIError as exc:
                retryable = attempt < LLM_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM API error ({}) — retry {}/{} in {:.0f}s",
                    exc,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):  # noqa: D102
        for attempt in range(LLM_MAX_RETRIES):
            try:
                return await super().request_stream(messages, model_settings, model_request_parameters, run_context)
            except ModelHTTPError as exc:
                retryable = exc.status_code in _TRANSIENT_STATUS and attempt < LLM_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM HTTP {} — retry {}/{} in {:.0f}s",
                    exc.status_code,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except ModelAPIError as exc:
                retryable = attempt < LLM_MAX_RETRIES - 1
                if not retryable:
                    raise
                delay = min(_RETRY_BASE_DELAY_S * 2**attempt, _RETRY_MAX_DELAY_S)
                logger.warning(
                    "LLM API error ({}) — retry {}/{} in {:.0f}s",
                    exc,
                    attempt + 1,
                    LLM_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
