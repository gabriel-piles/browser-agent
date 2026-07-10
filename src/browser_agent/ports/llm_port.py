from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic_ai.models import Model


class LlmPort(ABC):
    """Provider-agnostic LLM port.

    Implementations (Ollama, OpenRouter, Anthropic, ...) create a
    pydantic-ai :class:`Model`. The use case is responsible for
    building the :class:`Agent` and running it; the port only knows
    how to hand back a model.
    """

    @abstractmethod
    def get_model(self) -> Model:
        """Return the pydantic-ai Model to use in the Agent."""
