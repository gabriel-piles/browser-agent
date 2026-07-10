from __future__ import annotations

from abc import ABC, abstractmethod

from browser_agent.domain.html_snippet import HtmlSnippet


class WebInspectorPort(ABC):
    """Visits a URL with a real browser and returns cleaned HTML.

    Implementations launch a short-lived browser session (visible by
    default so the operator can watch it work), navigate to the URL,
    wait long enough for Single Page Apps to render, and hand the raw
    HTML to :class:`HtmlSnippet` for token optimisation. The port is
    deliberately silent on which browser library is used; the zendriver
    adapter is the only production implementation.
    """

    @abstractmethod
    async def inspect(self, url: str, settle_seconds: float = 2.0) -> HtmlSnippet:
        """Open ``url`` in a browser, return a token-optimised snippet.

        Implementations MUST:
        - launch a browser (visible by default), navigate to ``url``;
        - wait ``settle_seconds`` after navigation completes so that
          JavaScript-rendered content has time to attach to the DOM;
        - read the page's HTML and hand it to :class:`HtmlSnippet` so
          the cleaning rules apply once, in one place.
        """
