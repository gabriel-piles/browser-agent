"""Build a :class:`UwaziClient` from the env-backed configuration constants.

Hides the ``user=`` / ``password=`` / ``url=`` plumbing so the
Uwazi drivers can call ``UwaziClientFactory().build()`` and
move on. The credentials live in :mod:`browser_agent.configuration`
and originate from the project ``.env`` file.
"""

from __future__ import annotations

from uwazi_api.client import UwaziClient

from browser_agent.configuration import UWAZI_PASSWORD, UWAZI_URL, UWAZI_USER


class UwaziClientFactory:
    """Construct :class:`UwaziClient` instances from the env-backed constants."""

    def build(self) -> UwaziClient:
        """Return a fresh :class:`UwaziClient` using the configured credentials."""
        return UwaziClient(
            user=UWAZI_USER,
            password=UWAZI_PASSWORD,
            url=UWAZI_URL,
        )
