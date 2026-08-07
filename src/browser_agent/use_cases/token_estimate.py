"""Shared token-estimate helpers used by the compactor and the driver.

The Ollama OpenAI-compatible endpoint does not implement
``model.count_tokens`` (``OpenAIChatModel`` inherits the
``NotImplementedError`` default, and
``count_tokens_before_request=True`` would raise it), so we use the
same ``len // 4`` heuristic already used across the codebase for
usage logging. It is an estimate, not exact — the compactor budget
and the input-token limit are set far enough apart that a ±20%
estimation error can't let a real 120k+ prompt slip through.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` (``len // 4`` heuristic)."""
    return len(text) // 4


def estimate_message_tokens(messages: list[ModelMessage]) -> int:
    """Estimate the total token cost of a ``list[ModelMessage]``.

    Serializes each part's string content (system/user text, tool
    returns, thinking content, tool-call args) and sums
    :func:`estimate_tokens`. Non-string tool-return content is
    JSON-encoded so structured returns still count toward the budget.
    """
    total = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            total += _estimate_request(msg)
        elif isinstance(msg, ModelResponse):
            total += _estimate_response(msg)
    return total


def _estimate_request(msg: ModelRequest) -> int:
    total = 0
    for part in msg.parts:
        if isinstance(part, SystemPromptPart):
            total += estimate_tokens(part.content)
        elif isinstance(part, UserPromptPart):
            total += estimate_tokens(_user_content_to_str(part.content))
        elif isinstance(part, ToolReturnPart):
            total += estimate_tokens(_return_content_to_str(part.content))
    return total


def _estimate_response(msg: ModelResponse) -> int:
    total = 0
    for part in msg.parts:
        if isinstance(part, ThinkingPart):
            total += estimate_tokens(part.content)
        elif isinstance(part, ToolCallPart):
            total += estimate_tokens(part.tool_name)
            total += estimate_tokens(_call_args_to_str(part.args))
    return total


def _user_content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        else:
            parts.append(getattr(item, "content", "") or "")
    return "".join(parts)


def _return_content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content)
    except TypeError:
        return str(content)


def _call_args_to_str(args: Any) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args)
    except TypeError:
        return str(args)
