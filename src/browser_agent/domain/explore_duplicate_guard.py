"""Duplicate-action guard for the ``explore_page`` tool.

The model sometimes emits the exact same action (navigate/click/scroll)
multiple times in one response; pydantic-ai executes same-response
calls concurrently, so each duplicate hits the CDP tab and returns a
full page snapshot into context. This guard remembers the last few
action keys and reports an exact duplicate before the browser is
touched — the tool then returns a one-line suppression message instead
of another ~50k-char snapshot.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

RECENT_ACTION_WINDOW = 8

SUPPRESSION_MESSAGE = (
    "DUPLICATE action suppressed: this exact action was already executed recently and its result is in "
    "the conversation above. Do NOT repeat it — change the action (different selector/URL/scroll) or move on."
)


class ExploreDuplicateGuard(BaseModel):
    """FIFO of the most recent explore actions, used to suppress exact repeats.

    ``check`` reports whether a key is already in the window;
    ``remember`` appends a key and trims to the last
    ``RECENT_ACTION_WINDOW`` keys. Calls are sequential (the explore
    tool is ``sequential=True``), so no locking is needed.
    """

    recent_action_keys: list[str] = Field(
        default_factory=list,
        description="Most recent action keys, oldest first, capped at RECENT_ACTION_WINDOW.",
    )
    suppressed: int = Field(
        default=0,
        description="Count of duplicate actions suppressed since the guard was created.",
    )

    def check(self, key: str) -> bool:
        """Return True when ``key`` is already in the recent-action window."""
        return key in self.recent_action_keys

    def remember(self, key: str) -> None:
        """Append ``key`` to the window, trimming to the last 8 keys."""
        self.recent_action_keys.append(key)
        if len(self.recent_action_keys) > RECENT_ACTION_WINDOW:
            self.recent_action_keys = self.recent_action_keys[-RECENT_ACTION_WINDOW:]
