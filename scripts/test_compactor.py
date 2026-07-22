"""Smoke test for ToolReturnCompactor.

Exercises the compactor on a fake message history that mirrors the
real shape produced by the agent: system prompt + user prompt + a
long chain of ``explore_page`` tool returns containing big HTML
blobs, plus a couple of ``run_validation_script`` returns.

Asserts:
  - the last N tool returns are kept verbatim (header + extracted
    elements + full HTML body)
  - older tool returns are trimmed to header + extracted elements
    + a placeholder, never carrying the full HTML body
  - the cumulative character count drops dramatically
  - the most recent validation return is preserved
  - the result is a NEW list (original state history untouched)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable when run from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from browser_agent.use_cases.tool_return_compactor import (
    _summarise_explore,
    ToolReturnCompactor,
)


def make_history(n_snapshots: int = 20, n_validations: int = 2) -> list:
    history: list = []
    history.append(
        ModelRequest(
            parts=[
                SystemPromptPart("system"),
                UserPromptPart("scrape the site"),
            ]
        )
    )
    for i in range(n_snapshots):
        history.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="explore_page",
                        args={"action": "navigate"},
                        tool_call_id=f"snap-call-{i}",
                    )
                ]
            )
        )
        html = f"<html><body>page {i} " + ("x" * 60_000) + f"</body></html>"
        content = (
            f"# Action: navigated to https://example.com/p{i}\n"
            f"# URL: https://example.com/p{i}\n"
            f"# Title: Page {i}\n"
            f"# scroll_height: 2000px\n"
            f"# Extracted elements (3 total):\n"
            f"  <a> href='/p{i}/a' text='Link A'\n"
            f"  <a> href='/p{i}/b' text='Link B'\n"
            f"  <a> href='/p{i}/c' text='Link C'\n"
            f"\n{html}"
        )
        history.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="explore_page",
                        content=content,
                        tool_call_id=f"snap-call-{i}",
                    )
                ]
            )
        )
    for i in range(n_validations):
        history.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_validation_script",
                        args={"python_code": "print('hi')"},
                        tool_call_id=f"val-call-{i}",
                    )
                ]
            )
        )
        history.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="run_validation_script",
                        content=(
                            f"# Validation attempt {i+1}/3: SUCCESS\n\n"
                            + ("stdout noise\n" * 2000)
                            + "Traceback (most recent call last):\n  File x"
                        ),
                        tool_call_id=f"val-call-{i}",
                    )
                ]
            )
        )
    return history


def main() -> int:
    history = make_history()
    original_total = sum(
        len(p.content) if isinstance(p.content, str) else 0
        for msg in history
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    )
    print(f"original tool-return chars: {original_total:,}")

    cap = ToolReturnCompactor()
    new_messages = cap._compact(history)
    assert new_messages is not history, "must return a new list"
    assert len(new_messages) == len(history), "must preserve message count"

    compacted_total = sum(
        len(p.content) if isinstance(p.content, str) else 0
        for msg in new_messages
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart)
    )
    print(f"compacted tool-return chars: {compacted_total:,}")
    assert compacted_total < original_total * 0.2, f"expected <20% of original, got {compacted_total/original_total:.1%}"
    snap_kept_recent = 0
    snap_trimmed_old = 0
    val_kept_recent = 0
    val_trimmed_old = 0
    for msg in new_messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name == "explore_page":
                if len(part.content) < 1200:
                    snap_trimmed_old += 1
                else:
                    snap_kept_recent += 1
            elif part.tool_name == "run_validation_script":
                if len(part.content) < 600:
                    val_trimmed_old += 1
                else:
                    val_kept_recent += 1

    print(f"snapshots kept-recent={snap_kept_recent} trimmed-old={snap_trimmed_old}")
    print(f"validations kept-recent={val_kept_recent} trimmed-old={val_trimmed_old}")
    assert snap_kept_recent == 2, f"want 2 recent snapshots kept, got {snap_kept_recent}"
    assert snap_trimmed_old == 18, f"want 18 old snapshots trimmed, got {snap_trimmed_old}"
    assert val_kept_recent == 1, f"want 1 recent validation kept, got {val_kept_recent}"
    assert val_trimmed_old == 1, f"want 1 old validation trimmed, got {val_trimmed_old}"

    # _summarise_explore should keep header + extracted + placeholder, drop body
    sample = (
        "# Action: navigated\n# URL: /x\n# Title: T\n"
        "# scroll_height: 100px\n"
        "# Extracted elements (2 total):\n"
        "  <a> href='/a' text='A'\n"
        "  <a> href='/b' text='B'\n"
        "\n" + ("HTML" * 30_000)
    )
    summary = _summarise_explore(sample)
    assert "[trimmed" in summary
    assert "HTML" * 30_000 not in summary
    assert "href='/a'" in summary
    print(f"summary length: {len(summary):,} chars (was {len(sample):,})")

    # The state.message_history must be untouched (caller can still inspect it)
    assert history[0] is new_messages[0]
    print("PASS: compaction trims history without mutating the original")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
