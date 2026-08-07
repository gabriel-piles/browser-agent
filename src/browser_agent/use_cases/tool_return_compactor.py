"""A pydantic-ai capability that keeps tool returns bounded by a token budget.

Pydantic-AI's agent loop keeps every tool return in the message
history and resends the full history on each LLM request. The
``explore_page`` tool returns up to ``SNAPSHOT_MAX_CHARS`` of
cleaned HTML per call (~12.5k tokens); over a long run the cumulative
history can exceed the model's 1M-token context window.

This capability trims old tool returns in the message history copy
sent to the model, keeping the most recent returns full. The
underlying ``state.message_history`` is untouched, so the final
agent result still has the full audit trail.

Trimming is **budget-driven** (``COMPACT_INPUT_TOKEN_BUDGET``):
``estimate_message_tokens`` is the oracle, and the compactor applies
trims in priority order until the prompt is under budget:

1. Blank old ``ThinkingPart`` content (keep the 2 most recent) — the
   cheapest, biggest single win on reasoning models.
2. Summarise the oldest ``explore_page`` returns (keep 2 most recent).
3. Summarise the oldest ``run_validation_script`` returns (keep 1).
4. Summarise the oldest other tool returns (keep 1).
5. Aggressive fallback: summarise the most-recent returns too, keeping
   only the single most-recent of each bucket.

For ``explore_page`` returns we keep all metadata header lines plus
the ``# Extracted elements`` block and replace the HTML body with a
single placeholder. For any other tool return over
``COMPACT_MIN_TRIM_CHARS`` we keep only the first few non-empty lines
and drop the rest, with a placeholder appended.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from dataclasses import replace

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ThinkingPart, ToolReturnPart
from pydantic_ai.models import ModelRequestContext
from browser_agent.configuration import (
    COMPACT_HEAD_LINES,
    COMPACT_INPUT_TOKEN_BUDGET,
    COMPACT_KEEP_RECENT_VALIDATIONS,
    COMPACT_MAX_ANALYZE_LINES,
    COMPACT_MAX_EXTRACTED_LINES,
    COMPACT_MIN_TRIM_CHARS,
    COMPACT_TRUNCATED_PLACEHOLDER,
)
from browser_agent.use_cases.token_estimate import estimate_message_tokens

# Type var so the same capability works for both the step-0 agent
# (``AgentDeps``) and the verification agent (``VerificationAgentDeps``).
# The compactor never inspects ``ctx.deps``.
AnyAgentDeps = TypeVar("AnyAgentDeps", covariant=True)

_EXPLORE_TOOL = "explore_page"
_VALIDATION_TOOL = "run_validation_script"
_THINKING_KEEP_RECENT = 2
_EXPLORE_KEEP_RECENT = 2

_Summariser = Callable[[str], str]


class ToolReturnCompactor(AbstractCapability[AnyAgentDeps]):
    """pydantic-ai capability that trims old tool returns in the prompt."""

    def __init__(self, budget: int = COMPACT_INPUT_TOKEN_BUDGET) -> None:
        super().__init__()
        self._budget = budget

    async def before_model_request(
        self,
        ctx: RunContext[AnyAgentDeps],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        messages = request_context.messages
        if estimate_message_tokens(messages) <= self._budget:
            return request_context
        compacted = self._compact(messages)
        if compacted is messages:
            return request_context
        return replace(request_context, messages=compacted)

    def _compact(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        actions = _plan_trims(messages)
        return _apply_until_budget(messages, actions, self._budget)


def _apply_until_budget(
    messages: list[ModelMessage],
    actions: list[tuple[int, str, _Summariser]],
    budget: int,
) -> list[ModelMessage]:
    current = messages
    pending = list(actions)
    while pending and estimate_message_tokens(current) > budget:
        idx, tool, summarise = pending.pop(0)
        current = _apply_trim(current, idx, tool, summarise)
    return current


def _apply_trim(
    messages: list[ModelMessage],
    idx: int,
    tool: str | None,
    summarise: _Summariser,
) -> list[ModelMessage]:
    out: list[ModelMessage] = []
    for j, msg in enumerate(messages):
        if j == idx and isinstance(msg, ModelRequest):
            trimmed = _trim_request(msg, tool, summarise)
            out.append(trimmed)
            continue
        if j == idx and isinstance(msg, ModelResponse):
            out.append(_blank_thinking(msg))
            continue
        out.append(msg)
    return out


def _plan_trims(messages: list[ModelMessage]) -> list[tuple[int, str, _Summariser]]:
    snaps, vals, others = _classify_indices(messages)
    thinkers = _thinking_indices(messages)
    actions: list[tuple[int, str, _Summariser]] = []
    actions += _thinking_actions(thinkers)
    actions += _bucket_actions(snaps, _EXPLORE_TOOL, _summarise_explore, _EXPLORE_KEEP_RECENT)
    actions += _bucket_actions(vals, _VALIDATION_TOOL, _summarise_generic, COMPACT_KEEP_RECENT_VALIDATIONS)
    actions += _bucket_actions(others, None, _summarise_generic, 1)
    actions += _aggressive_actions(snaps, vals, others)
    return actions


def _thinking_actions(thinkers: set[int]) -> list[tuple[int, str, _Summariser]]:
    ordered = sorted(thinkers)
    keep = max(len(ordered) - _THINKING_KEEP_RECENT, 0)
    return [(idx, "", _blank) for idx in ordered[:keep]]


def _bucket_actions(
    indices: set[int],
    tool: str | None,
    summarise: _Summariser,
    keep_recent: int,
) -> list[tuple[int, str, _Summariser]]:
    ordered = sorted(indices)
    keep = max(len(ordered) - keep_recent, 0)
    return [(idx, tool or "", summarise) for idx in ordered[:keep]]


def _aggressive_actions(
    snaps: set[int],
    vals: set[int],
    others: set[int],
) -> list[tuple[int, str, _Summariser]]:
    actions: list[tuple[int, str, _Summariser]] = []
    actions += _aggressive_bucket(snaps, _EXPLORE_TOOL, _summarise_explore)
    actions += _aggressive_bucket(vals, _VALIDATION_TOOL, _summarise_generic)
    actions += _aggressive_bucket(others, None, _summarise_generic)
    return actions


def _aggressive_bucket(
    indices: set[int],
    tool: str | None,
    summarise: _Summariser,
) -> list[tuple[int, str, _Summariser]]:
    ordered = sorted(indices)
    if len(ordered) <= 1:
        return []
    keep = ordered[-1]
    return [(idx, tool or "", summarise) for idx in ordered if idx != keep]


def _blank(content: str) -> str:
    return "[trimmed thinking]"


def _blank_thinking(msg: ModelResponse) -> ModelResponse:
    new_parts = [replace(p, content="[trimmed thinking]") if isinstance(p, ThinkingPart) else p for p in msg.parts]
    return replace(msg, parts=new_parts)


def _classify_indices(messages: list[ModelMessage]) -> tuple[set[int], set[int], set[int]]:
    snaps: set[int] = set()
    vals: set[int] = set()
    others: set[int] = set()
    for idx, msg in enumerate(messages):
        if isinstance(msg, ModelRequest):
            _collect_message_bucket(idx, msg, snaps, vals, others)
    return snaps, vals, others


def _thinking_indices(messages: list[ModelMessage]) -> set[int]:
    """Indices of ``ModelResponse`` messages that carry a ``ThinkingPart``."""
    return {
        idx
        for idx, msg in enumerate(messages)
        if isinstance(msg, ModelResponse) and any(isinstance(p, ThinkingPart) for p in msg.parts)
    }


def _collect_message_bucket(
    idx: int,
    msg: ModelRequest,
    snaps: set[int],
    vals: set[int],
    others: set[int],
) -> None:
    for part in msg.parts:
        _add_part_bucket(idx, part, snaps, vals, others)


def _add_part_bucket(
    idx: int,
    part,
    snaps: set[int],
    vals: set[int],
    others: set[int],
) -> None:
    bucket = _trim_bucket(part)
    if bucket is None:
        return
    if bucket == _EXPLORE_TOOL:
        snaps.add(idx)
    elif bucket == _VALIDATION_TOOL:
        vals.add(idx)
    else:
        others.add(idx)


def _trim_bucket(part: ToolReturnPart) -> str | None:
    """Return the tool name if the part's content is large enough to trim."""
    if not isinstance(part.content, str):
        return None
    if len(part.content) < COMPACT_MIN_TRIM_CHARS:
        return None
    return part.tool_name


def _trim_request(msg: ModelRequest, tool_name: str | None, summarise) -> ModelRequest:
    new_parts = []
    changed = False
    for part in msg.parts:
        rewritten = _maybe_rewrite_part(part, tool_name, summarise)
        if rewritten is None:
            new_parts.append(part)
        else:
            new_parts.append(rewritten)
            changed = True
    if not changed:
        return msg
    return replace(msg, parts=new_parts)


def _maybe_rewrite_part(part, tool_name, summarise):
    if not isinstance(part, ToolReturnPart):
        return None
    if tool_name is not None and part.tool_name != tool_name:
        return None
    content = part.content
    if not isinstance(content, str) or len(content) < COMPACT_MIN_TRIM_CHARS:
        return None
    new_content = summarise(content)
    if new_content is content:
        return None
    return replace(part, content=new_content)


def _summarise_explore(content: str) -> str:
    """Keep header lines + extracted elements, drop the HTML body.

    ``inspect`` returns lose the HTML snippet but keep their metadata
    headers, which is fine — the agent can call ``inspect`` again to
    re-fetch the snippet.  ``analyze`` returns are dispatched to
    :func:`_summarise_analyze` which trims each section's element list.
    """
    if _is_analyze_return(content):
        return _summarise_analyze(content)
    kept: list[str] = []
    state = _ExploreState()
    for line in content.splitlines():
        if state.step(line, kept):
            break
    kept.append(COMPACT_TRUNCATED_PLACEHOLDER)
    return "\n".join(kept)


def _flush_analyze_placeholder(kept, seen, trimmed):
    """Emit a ``[{n} more trimmed]`` line when a section was capped."""
    if trimmed:
        kept.append(f"[{trimmed} more trimmed]")


def _summarise_analyze(content: str) -> str:
    """Keep all section headers, trim each section's element lines.

    The analyze return format is ``#`` header lines (section name +
    count) followed by indented ``  <`` element lines.  We keep every
    header but only the first ``COMPACT_MAX_ANALYZE_LINES`` element
    lines per section; when a section is capped we emit a
    ``[{n} more trimmed]`` placeholder at its boundary so the agent
    knows how many elements were dropped.
    """
    kept: list[str] = []
    section_seen = 0
    section_trimmed = 0
    for line in content.splitlines():
        if line.startswith("#"):
            _flush_analyze_placeholder(kept, section_seen, section_trimmed)
            kept.append(line)
            section_seen = 0
            section_trimmed = 0
            continue
        if line.startswith("  "):
            if section_seen < COMPACT_MAX_ANALYZE_LINES:
                kept.append(line)
                section_seen += 1
            else:
                section_trimmed += 1
            continue
        if line.strip():
            _flush_analyze_placeholder(kept, section_seen, section_trimmed)
            kept.append(line)
            section_seen = 0
            section_trimmed = 0
            continue
        kept.append(line)
    _flush_analyze_placeholder(kept, section_seen, section_trimmed)
    return "\n".join(kept)


def _is_analyze_return(content: str) -> bool:
    """Detect if the explore_page output is a structured analysis return."""
    first = (content.splitlines() or [""])[0]
    return "analyzed page structure" in first


def _summarise_generic(content: str) -> str:
    """Keep the first few non-empty lines, drop the rest."""
    kept: list[str] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        kept.append(line)
        if len(kept) >= COMPACT_HEAD_LINES:
            break
    kept.append(COMPACT_TRUNCATED_PLACEHOLDER)
    return "\n".join(kept)


class _ExploreState:
    """Parses one explore_page output, keeping headers + extracted elements.

    The output has three sections: metadata headers (``#`` lines),
    an optional extracted-elements block (``# Extracted elements``
    header + ``  <`` element lines), and the HTML body.  We keep the
    first two and stop at the HTML body.
    """

    _METADATA = "metadata"
    _EXTRACTED = "extracted"

    __slots__ = ("phase", "extracted_count")

    def __init__(self) -> None:
        self.phase = self._METADATA
        self.extracted_count = 0

    def step(self, line: str, kept: list[str]) -> bool:
        """Process one line. Return True when the reader must stop."""
        if self.phase == self._METADATA:
            return self._step_metadata(line, kept)
        return self._step_extracted(line, kept)

    def _step_metadata(self, line: str, kept: list[str]) -> bool:
        if not line.strip():
            return False
        if line.startswith("# Extracted elements"):
            kept.append(line)
            self.phase = self._EXTRACTED
            return False
        if line.startswith("#"):
            kept.append(line)
            return False
        return True

    def _step_extracted(self, line: str, kept: list[str]) -> bool:
        if not line.strip():
            return False
        if not line.startswith("  <"):
            return True
        if self.extracted_count >= COMPACT_MAX_EXTRACTED_LINES:
            return True
        kept.append(line)
        self.extracted_count += 1
        return False
