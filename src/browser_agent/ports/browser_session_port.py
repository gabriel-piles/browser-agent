"""Abstract port for a persistent, interactive browser session.

Unlike the old :class:`WebInspectorPort` (which launched a fresh
browser per call), a :class:`BrowserSessionPort` keeps one Chrome
instance alive for the lifetime of the agent run. This lets the
agent navigate, click filters, scroll, fill forms, and extract
elements — all in the same tab — *before* writing any validation
script.

The session is an async context manager: callers ``await session.start()``
to launch Chrome and ``await session.close()`` to tear it down.
Between those calls, :meth:`perform` drives the page.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from browser_agent.domain.page_action import PageAction
from browser_agent.domain.page_snapshot import PageSnapshot


class BrowserSessionPort(ABC):
    """A reusable browser tab the agent can drive interactively."""

    @abstractmethod
    async def start(self) -> None:
        """Launch the browser. Call once before any :meth:`perform`."""

    @abstractmethod
    async def close(self) -> None:
        """Tear the browser down. Always safe to call, even if never started."""

    @abstractmethod
    async def perform(self, action: PageAction) -> PageSnapshot:
        """Execute ``action`` in the current tab and return a snapshot.

        Implementations MUST:
        - reuse the same browser/tab across calls (no per-call launch);
        - for ``navigate``, open ``action.url`` in the existing tab;
        - for ``click``/``fill``/``select``/``scroll``, mutate the page
          in place and return the resulting HTML;
        - for ``extract``, run the CSS selector and return matching
          elements' text + href (no full HTML dump);
        - for ``wait``, sleep and return the current HTML.
        """

    @abstractmethod
    async def get_cookies(self, urls: list[str] | None = None) -> list[dict[str, str]]:
        """Return cookies from the active browser session as a list of dicts.

        Each dict has keys: name, value, domain, path, expires, http_only,
        secure, same_site. When ``urls`` is None, returns cookies for the
        current page URL. Used by the download tool to share the browser's
        auth/anti-bot cookies with curl_cffi.
        """
