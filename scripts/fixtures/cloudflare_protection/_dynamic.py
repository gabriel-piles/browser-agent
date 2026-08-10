"""Dynamic fixture renderer for cloudflare_protection scenario.

Simulates a Cloudflare-style challenge page: the first request
returns a 503 with a JS challenge that sets a cookie. Subsequent
requests with the cookie (or after a short delay) return the real
content. Tests that the agent's browser (zendriver) can execute
the JS challenge and access the real content.
"""

from __future__ import annotations

import time

_CHALLENGE_DELAY_S = 2.0
_first_request_time: dict[str, float] = {}


def custom_route(path: str, query: dict[str, list[str]]) -> tuple[str, str, int] | None:
    """Return a challenge page on first visit, real content after JS executes."""
    if path != "/" and path != "/index.html":
        return None
    scenario = query.get("scenario", ["cloudflare_protection"])[0]
    now = time.time()
    first_time = _first_request_time.get(scenario, 0)
    if first_time == 0:
        _first_request_time[scenario] = now
        return _challenge_page(scenario), "text/html; charset=utf-8", 503
    if now - first_time < _CHALLENGE_DELAY_S:
        return _challenge_page(scenario), "text/html; charset=utf-8", 503
    return None  # Fall through to normal index rendering


def _challenge_page(scenario: str) -> str:
    """Return a Cloudflare-style challenge page with JS redirect."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Just a moment...</title></head><body>"
        "<div id='cf-challenge'>Checking your browser...</div>"
        "<script>"
        "setTimeout(function() {"
        f"  window.location.href = '/?scenario={scenario}';"
        f"}}, {_CHALLENGE_DELAY_S * 1000:.0f});"
        "</script></body></html>"
    )


def index(query: dict[str, list[str]]) -> str:
    """Render the real content page (served after the challenge clears)."""
    scenario = query.get("scenario", ["cloudflare_protection"])[0]
    rows = ""
    for i in range(1, 11):
        rows += (
            f'<div class="item"><h3><a href="/doc/{i}?scenario={scenario}">Protected Document {i}</a></h3>'
            f'<span class="date">2024-{(i % 12) + 1:02d}-15</span></div>\n'
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Protected Archive</title></head><body>"
        "<div class='container'><h1>Protected Document Archive</h1>"
        f"<div class='item-list'>{rows}</div></div></body></html>"
    )


def fragment(query: dict[str, list[str]]) -> str:
    """Not used for this scenario."""
    return ""
