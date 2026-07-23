"""A pydantic-ai capability that keeps tool returns bounded.

Pydantic-AI's agent loop keeps every tool return in the message
history and resends the full history on each LLM request. The
``explore_page`` tool returns up to ``SNAPSHOT_MAX_CHARS`` of
cleaned HTML per call; over a long run the cumulative history
exceeds the model's context window (we hit 270k tokens against
a 262k cap).

This capability trims old tool returns in the message history copy
sent to the model, keeping only the most recent returns full. The
underlying ``state.message_history`` is untouched, so the final
agent result still has the full audit trail.

For ``explore_page`` returns we keep all metadata header lines
(Action, URL, Title, summary, URL CHANGED, scroll_height, ERROR)
plus the ``# Extracted elements`` block (header + up to
``COMPACT_MAX_EXTRACTED_LINES`` element lines with text + href) —
the structural clues the agent needs to remember what each page
looked like — and replace the HTML body with a single placeholder.

For any other tool return over ``COMPACT_MIN_TRIM_CHARS`` we keep
only the first few non-empty lines and drop the rest, with a
placeholder appended.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import ModelRequestContext
from browser_agent.configuration import (
    COMPACT_HEAD_LINES,
    COMPACT_KEEP_RECENT_SNAPSHOTS,
    COMPACT_KEEP_RECENT_STRUCTURED,
    COMPACT_KEEP_RECENT_VALIDATIONS,
    COMPACT_MAX_EXTRACTED_LINES,
    COMPACT_MIN_TRIM_CHARS,
    COMPACT_STRUCTURED_MAX_TRIM_CHARS,
    COMPACT_TRUNCATED_PLACEHOLDER,
)
from browser_agent.use_cases.agent_deps import AgentDeps

_EXPLORE_TOOL = "explore_page"
_VALIDATION_TOOL = "run_validation_script"

_NEVER_TRIM = 10**9


@dataclass(frozen=True)
class _CutPlan:
    """Per-bucket index sets + the lowest index to KEEP for each."""

    snaps: set[int] = field(default_factory=set)
    vals: set[int] = field(default_factory=set)
    others: set[int] = field(default_factory=set)
    snap_cut: int = _NEVER_TRIM
    val_cut: int = _NEVER_TRIM
    oth_cut: int = _NEVER_TRIM


class ToolReturnCompactor(AbstractCapability[AgentDeps]):
    """pydantic-ai capability that trims old tool returns in the prompt."""

    async def before_model_request(
        self,
        ctx: RunContext[AgentDeps],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        messages = request_context.messages
        if len(messages) <= 2:
            return request_context
        compacted = self._compact(messages)
        if compacted is messages:
            return request_context
        return replace(request_context, messages=compacted)

    @staticmethod
    def _compact(messages: list[ModelMessage]) -> list[ModelMessage]:
        plan = _build_plan(messages)
        if plan is None:
            return messages
        out: list[ModelMessage] = []
        for idx, msg in enumerate(messages):
            rewritten = _maybe_rewrite(idx, msg, plan)
            out.append(rewritten if rewritten is not None else msg)
        return out


def _build_plan(messages: list[ModelMessage]) -> _CutPlan | None:
    """Classify messages and decide which indices to trim.

    Uses ``COMPACT_KEEP_RECENT_STRUCTURED`` for explore_page returns
    so structured analysis (which is low-token) stays visible longer.
    """
    snaps, vals, others = _classify_indices(messages)
    if not snaps and not vals and not others:
        return None
    return _CutPlan(
        snaps=snaps,
        vals=vals,
        others=others,
        snap_cut=_cut_index(snaps, COMPACT_KEEP_RECENT_STRUCTURED),
        val_cut=_cut_index(vals, COMPACT_KEEP_RECENT_VALIDATIONS),
        oth_cut=_cut_index(others, 0),
    )


def _classify_indices(messages: list[ModelMessage]) -> tuple[set[int], set[int], set[int]]:
    snaps: set[int] = set()
    vals: set[int] = set()
    others: set[int] = set()
    for idx, msg in enumerate(messages):
        _collect_message_bucket(idx, msg, snaps, vals, others)
    return snaps, vals, others


def _collect_message_bucket(
    idx: int,
    msg: ModelMessage,
    snaps: set[int],
    vals: set[int],
    others: set[int],
) -> None:
    if not _has_trimmable_part(msg):
        return
    for part in msg.parts:
        _add_part_bucket(idx, part, snaps, vals, others)


def _has_trimmable_part(msg: ModelMessage) -> bool:
    return isinstance(msg, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in msg.parts)


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


def _cut_index(indices: set[int], keep_recent: int) -> int:
    """Return the lowest index to KEEP; older ones get trimmed."""
    if keep_recent <= 0 or len(indices) <= keep_recent:
        return _NEVER_TRIM
    return sorted(indices, reverse=True)[keep_recent - 1]


def _maybe_rewrite(idx: int, msg: ModelMessage, plan: _CutPlan) -> ModelRequest | None:
    if not isinstance(msg, ModelRequest):
        return None
    if idx in plan.snaps and idx < plan.snap_cut:
        return _trim_request(msg, _EXPLORE_TOOL, _summarise_explore)
    if idx in plan.vals and idx < plan.val_cut:
        return _trim_request(msg, _VALIDATION_TOOL, _summarise_generic)
    if idx in plan.others and idx < plan.oth_cut:
        return _trim_request(msg, None, _summarise_generic)
    return None


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

    Structured ``analyze`` returns are kept full (they are small and
    contain selectors the agent needs).  ``inspect`` returns lose the
    HTML snippet but keep their metadata headers, which is fine — the
    agent can call ``inspect`` again to re-fetch the snippet.
    """
    if _is_analyze_return(content):
        return content
    kept: list[str] = []
    state = _ExploreState()
    for line in content.splitlines():
        if state.step(line, kept):
            break
    kept.append(COMPACT_TRUNCATED_PLACEHOLDER)
    return "\n".join(kept)


def _is_analyze_return(content: str) -> bool:
    """Detect if the explore_page output is a structured analysis return."""
    first = (content.splitlines() or [""])[0]
    return "analyzed page structure" in first
    kept.append(COMPACT_TRUNCATED_PLACEHOLDER)
    return "\n".join(kept)


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
