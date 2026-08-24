"""Selects the LLM adapter for the configured :data:`LLM_PROVIDER`."""

from __future__ import annotations

import os

from loguru import logger

from browser_agent.adapters.llm.ollama_adapter import OllamaAdapter
from browser_agent.adapters.llm.openrouter_adapter import OpenRouterAdapter
from browser_agent.adapters.llm.opencode_zen_adapter import OpenCodeZenAdapter
from browser_agent.configuration import LLM_PROVIDER, LLM_PROVIDER_ENV_KEYS, LLM_PROVIDERS
from browser_agent.ports.llm_port import LlmPort

_ADAPTERS = {
    "ollama": OllamaAdapter,
    "opencode": OpenCodeZenAdapter,
    "openrouter": OpenRouterAdapter,
}


def build_llm() -> LlmPort:
    """Return the adapter for ``LLM_PROVIDER`` after checking every API key."""
    if LLM_PROVIDER not in _ADAPTERS:
        raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'; expected one of {LLM_PROVIDERS}")
    _check_provider_keys(LLM_PROVIDER)
    return _ADAPTERS[LLM_PROVIDER]()


def _check_provider_keys(active: str) -> None:
    """Raise only when the active provider lacks its API key.

    Non-active providers are ignored; their adapters fail on their own if
    ever selected, so warning about them up front is noise.
    """
    env_var = LLM_PROVIDER_ENV_KEYS.get(active)
    if env_var and not os.environ.get(env_var):
        raise RuntimeError(f"{env_var} must be set in the environment or .env file (LLM_PROVIDER is '{active}')")
