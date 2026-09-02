"""Shared OpenAI-compatible model construction with a model fallback chain."""

from __future__ import annotations

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.openai import OpenAIProvider

from browser_agent.adapters.llm.retrying_chat_model import RetryingChatModel
from browser_agent.configuration import (
    LLM_CONNECT_TIMEOUT_S,
    LLM_MAX_RETRIES,
    LLM_READ_TIMEOUT_S,
    MODEL,
)

_ONE_MODEL_IN_CHAIN = 1


def _build_client(api_key: str, base_url: str) -> AsyncOpenAI:
    # max_retries=0: the app-layer RetryingChatModel already retries
    # LLM_MAX_RETRIES times on transient errors, so leaving the transport
    # layer on retries would compound into LLM_MAX_RETRIES**2 attempts.
    # A short connect timeout fails an unreachable endpoint fast; a long
    # read timeout tolerates slow non-streaming reasoning turns.
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=httpx.Timeout(
            connect=LLM_CONNECT_TIMEOUT_S,
            read=LLM_READ_TIMEOUT_S,
            write=LLM_READ_TIMEOUT_S,
            pool=LLM_CONNECT_TIMEOUT_S,
        ),
    )


def build_fallback_chain(api_key: str, base_url: str) -> Model:
    """Build one :class:`RetryingChatModel` per configured model and chain them.

    Each model retries transient errors on its own (``LLM_MAX_RETRIES``); only
    after its budget is exhausted does the :class:`FallbackModel` move the same
    request to the next model in :data:`MODEL`, so a dead or timing-out model
    never aborts the run.
    """
    models = [
        RetryingChatModel(name, provider=OpenAIProvider(openai_client=_build_client(api_key, base_url))) for name in MODEL
    ]
    if len(models) == _ONE_MODEL_IN_CHAIN:
        return models[0]
    return FallbackModel(models[0], *models[1:])
