"""Key builder and suppression message for the duplicate-action guard.

The domain model (:mod:`browser_agent.domain.explore_duplicate_guard`)
holds the window state; this module defines the action-derived identity
key and the fixed suppression string returned to the model.
"""

from __future__ import annotations

from browser_agent.domain.explore_duplicate_guard import SUPPRESSION_MESSAGE
from browser_agent.domain.page_action import PageAction


def action_key(action: PageAction) -> str:
    """Return the identity key for one action — its full JSON form."""
    return action.model_dump_json()


def suppression_message() -> str:
    """Return the fixed message telling the model the action was already done."""
    return SUPPRESSION_MESSAGE
