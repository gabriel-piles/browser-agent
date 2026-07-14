"""Anti-bot / human-challenge detection and bypass helpers.

This module is deliberately split into pure detection logic and an
async browser-facing layer so it can be used both inside
:class:`ZendriverBrowserSession` and as a standalone probe from the
``human_challenge`` driver.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from browser_agent.domain.challenge_status import ChallengeStatus

# ---------------------------------------------------------------------------
# Detection heuristics
# ---------------------------------------------------------------------------

_CHALLENGE_TITLES = {
    "just a moment": ("cloudflare_iuam", 0.95),
    "attention required!": ("cloudflare_iuam", 0.95),
    "checking your browser": ("cloudflare_iuam", 0.85),
    "please wait": ("generic_wait", 0.6),
    "verify you are human": ("generic_wait", 0.9),
    "human verification": ("generic_wait", 0.9),
    "are you human": ("generic_wait", 0.9),
    "security check": ("generic_wait", 0.7),
    "captcha": ("unknown", 0.6),
}

_CHALLENGE_SELECTORS = {
    "#cf-challenge-running": "cloudflare_turnstile",
    ".cf-challenge-running": "cloudflare_turnstile",
    "#turnstile-wrapper": "cloudflare_turnstile",
    "input[name='cf-turnstile-response']": "cloudflare_turnstile",
    "input[id^='cf-chl-widget-']": "cloudflare_turnstile",
    "iframe[src*='challenges.cloudflare.com/cdn-cgi/challenge-platform']:not([src*='turnstile'])": "cloudflare_iuam",
    "iframe[src*='challenges.cloudflare.com/cdn-cgi/challenge-platform'][src*='turnstile']": "cloudflare_turnstile",
    ".g-recaptcha": "recaptcha",
    "iframe[src*='google.com/recaptcha']": "recaptcha",
    "iframe[src*='recaptcha.net']": "recaptcha",
    ".h-captcha": "hcaptcha",
    "iframe[src*='hcaptcha.com/checkcaptcha']": "hcaptcha",
    "#captcha": "unknown",
    ".captcha": "unknown",
}

_CHALLENGE_TEXT_PATTERNS = [
    (r"just\s+a\s+moment", "cloudflare_iuam", 0.95),
    (r"performing\s+security\s+verification", "cloudflare_turnstile", 0.95),
    (r"verifies\s+you\s+are\s+not\s+a\s+bot", "cloudflare_turnstile", 0.95),
    (r"waiting\s+for\s+.*?\s+to\s+respond", "cloudflare_iuam", 0.85),
    (r"please\s+verify\s+(?:you are|that you'?re)\s+human", "generic_wait", 0.9),
    (r"verify\s+(?:you are|you'?re)\s+human", "generic_wait", 0.85),
    (r"prove\s+(?:you are|you'?re)\s+not\s+a\s+robot", "generic_wait", 0.85),
    (r"checking\s+your\s+browser\s+before\s+accessing", "cloudflare_iuam", 0.9),
    (r"ray\s+id", "cloudflare_iuam", 0.8),
    (r"cf-turnstile", "cloudflare_turnstile", 0.95),
    (r"turnstile", "cloudflare_turnstile", 0.75),
    (r"recaptcha", "recaptcha", 0.9),
    (r"hcaptcha", "hcaptcha", 0.9),
    (r"g-recaptcha", "recaptcha", 0.95),
    (r"im\s+under\s+attack", "cloudflare_iuam", 0.9),
    (r"ddos\s+protection", "cloudflare_iuam", 0.7),
    (r"please\s+enable\s+cookies", "generic_wait", 0.5),
]

_CHALLENGE_HOSTS = [
    ("challenges.cloudflare.com/cdn-cgi/challenge-platform", "cloudflare_turnstile"),
    ("cloudflarechallenge.com", "cloudflare_iuam"),
    ("google.com/recaptcha", "recaptcha"),
    ("recaptcha.net", "recaptcha"),
    ("hcaptcha.com/checkcaptcha", "hcaptcha"),
]


class ChallengeDetector:
    """Pure-text / HTML challenge detector.

    Works on any string (URL, title, raw HTML) without needing a live
    browser. This makes it cheap to call after every snapshot.
    """

    def detect(self, url: str, title: str, raw_html: str) -> ChallengeStatus:
        details: list[str] = []
        hits: dict[str, float] = {}
        text = f"{title} {raw_html}".lower()

        # Title heuristics
        lower_title = title.lower()
        for marker, (kind, confidence) in _CHALLENGE_TITLES.items():
            if marker in lower_title:
                hits[kind] = max(hits.get(kind, 0.0), confidence)
                details.append(f"title contains {marker!r} -> {kind}")

        # Selector heuristics via regex (we don't have a DOM here)
        for selector, kind in _CHALLENGE_SELECTORS.items():
            if self._selector_matches(selector, raw_html):
                hits[kind] = max(hits.get(kind, 0.0), 0.85)
                details.append(f"selector matched {selector!r} -> {kind}")

        # Text patterns
        for pattern, kind, confidence in _CHALLENGE_TEXT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                hits[kind] = max(hits.get(kind, 0.0), confidence)
                details.append(f"text pattern {pattern!r} -> {kind}")

        # Host heuristics from URL
        lower_url = url.lower()
        for host, kind in _CHALLENGE_HOSTS:
            if host in lower_url:
                hits[kind] = max(hits.get(kind, 0.0), 0.9)
                details.append(f"URL host {host!r} -> {kind}")

        if not hits:
            return ChallengeStatus.none()

        # Pick highest-confidence kind
        best_kind = max(hits, key=hits.get)  # type: ignore[arg-type]
        confidence = hits[best_kind]
        return ChallengeStatus(
            is_challenge=True,
            kind=best_kind,
            confidence=confidence,
            details=details,
        )

    @staticmethod
    def _selector_matches(selector: str, raw_html: str) -> bool:
        """Naive check: turn a CSS selector into a substring search.

        This is intentionally approximate and cheap. For exact DOM
        checks, call :meth:`ChallengeDetector.detect` plus a browser
        query via :class:`HumanChallengeBypass`.
        """
        # Strip leading # / . for id/class markers
        marker = selector.lstrip("#.")
        # Handle attribute selectors: keep the attribute name/value if simple
        # e.g. "input[name='cf-turnstile-response']" -> "cf-turnstile-response"
        attr_match = re.search(r"\[(.+?)\]", marker)
        if attr_match:
            attr_content = attr_match.group(1)
            # If it looks like name='value' or id^='prefix', extract the value
            val_match = re.search(r"[\^\$\*]?=\s*['\"]([^'\"]+)['\"]", attr_content)
            if val_match:
                return val_match.group(1) in raw_html
            return attr_content in raw_html
        # Handle prefix selectors like input[id^='cf-chl-widget-']
        prefix_match = re.search(r"\[\w+[\^\$\*]=['\"]([^'\"]+)['\"]\]", selector)
        if prefix_match:
            return prefix_match.group(1) in raw_html
        # Simple tag/class/id: strip tag and first bracket
        marker = re.split(r"[\[\(\:>]", marker, maxsplit=1)[0]
        if not marker:
            return False
        return marker in raw_html


# ---------------------------------------------------------------------------
# Browser-facing bypass
# ---------------------------------------------------------------------------


@dataclass
class BypassConfig:
    """Tunable knobs for challenge bypass attempts."""

    max_wait_rounds: int = 3
    wait_seconds: float = 10.0
    settle_seconds: float = 2.0
    click_any_checkbox: bool = True
    allow_reload: bool = True
    interactive_pause: bool = True
    interactive_timeout_seconds: float = 120.0
    real_user_agent: str | None = None
    profile_dir: Path | None = None


_REALISTIC_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


class HumanChallengeBypass:
    """Wrap a zendriver tab to detect and auto-resolve anti-bot pages.

    The bypass is intentionally conservative: it waits for known
    challenges to self-resolve using the browser's stealth profile and
    real user data dir. It does NOT solve image/audio captchas. If a
    challenge remains after the wait budget, it raises
    :class:`UnsolvedChallengeError`.
    """

    def __init__(self, detector: ChallengeDetector | None = None, config: BypassConfig | None = None) -> None:
        self.detector = detector or ChallengeDetector()
        self.config = config or BypassConfig()
        self._attempted_urls: set[str] = set()

    async def wait_for_clear(self, tab: Any, url: str, title: str, raw_html: str) -> ChallengeStatus:
        """Return ``ChallengeStatus.none()`` once the page is no longer a challenge.

        If the page is not a challenge on first check, returns
        immediately. Otherwise it waits up to
        ``max_wait_rounds * wait_seconds`` and re-checks. Between rounds
        it may refresh the page, click visible challenge checkboxes
        (Turnstile/hCaptcha one-click), and settle.

        If the challenge persists after automatic attempts and
        ``interactive_pause`` is enabled, the helper polls until the
        operator manually completes the challenge in the visible
        browser window.
        """
        status = self.detector.detect(url, title, raw_html)
        if not status:
            return status

        if url in self._attempted_urls:
            logger.info("challenge bypass already attempted for {url}; skipping auto-bypass", url=url)
            return status
        self._attempted_urls.add(url)

        logger.info(
            "human-challenge detected kind={kind} confidence={conf:.2f} details={details}",
            kind=status.kind,
            conf=status.confidence,
            details=status.details,
        )

        for round_ in range(1, self.config.max_wait_rounds + 1):
            logger.info(
                "human-challenge wait round {round}/{total} for {seconds}s",
                round=round_,
                total=self.config.max_wait_rounds,
                seconds=self.config.wait_seconds,
            )
            await tab.sleep(self.config.wait_seconds)
            await self._settle()

            if self.config.click_any_checkbox:
                await self._click_visible_checkbox(tab)
                await self._click_turnstile_widget(tab)
                await self._settle()

            # Refresh once on the second round to give Turnstile a clean
            # start with a now-warmed profile.
            if round_ == 2 and self.config.allow_reload:
                logger.info("human-challenge refreshing page")
                try:
                    await tab.reload()
                except Exception as exc:
                    logger.debug("human-challenge reload failed: {exc}", exc=exc)
                await self._settle()

            current_url = getattr(tab, "url", "") or ""
            current_title = await self._safe_title(tab)
            current_html = await self._safe_html(tab)
            status = self.detector.detect(current_url, current_title, current_html)
            if not status:
                logger.info("human-challenge cleared after round {round}", round=round_)
                return ChallengeStatus.none()

        if self.config.interactive_pause:
            logger.warning(
                "human-challenge auto-bypass failed; pausing {seconds}s for manual completion",
                seconds=self.config.interactive_timeout_seconds,
            )
            status = await self._interactive_wait(tab)
            if not status:
                return ChallengeStatus.none()

        logger.warning(
            "human-challenge persisted after {rounds} rounds — raising",
            rounds=self.config.max_wait_rounds,
        )
        raise UnsolvedChallengeError(status)

    async def _interactive_wait(self, tab: Any) -> ChallengeStatus:
        deadline = asyncio.get_event_loop().time() + self.config.interactive_timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            await tab.sleep(2.0)
            current_url = getattr(tab, "url", "") or ""
            current_title = await self._safe_title(tab)
            current_html = await self._safe_html(tab)
            status = self.detector.detect(current_url, current_title, current_html)
            if not status:
                logger.info("human-challenge cleared during interactive pause")
                return ChallengeStatus.none()
            logger.info(
                "human-challenge still present; waiting for operator ({remaining:.0f}s left)",
                remaining=deadline - asyncio.get_event_loop().time(),
            )
        current_url = getattr(tab, "url", "") or ""
        current_title = await self._safe_title(tab)
        current_html = await self._safe_html(tab)
        return self.detector.detect(current_url, current_title, current_html)

    async def _settle(self) -> None:
        if self.config.settle_seconds:
            await asyncio.sleep(self.config.settle_seconds)

    async def _safe_title(self, tab: Any) -> str:
        try:
            return (await tab.evaluate("document.title")) or ""
        except Exception:
            return ""

    async def _safe_html(self, tab: Any) -> str:
        try:
            return await tab.get_content()
        except Exception:
            return ""

    async def _click_visible_checkbox(self, tab: Any) -> None:
        """Click the first visible challenge checkbox if present.

        Matches Turnstile's 'Verify you are human' checkbox and the
        generic hCaptcha/recaptcha wrappers. Uses JS to avoid relying on
        zendriver's element visibility APIs.
        """
        try:
            result = await tab.evaluate(
                "(() => {"
                "  const selectors = ["
                "    'input[type=checkbox]',"
                "    '.cf-turnstile input',"
                "    '.cf-challenge-running input',"
                "    '[class*=turnstile] input[type=checkbox]',"
                "    '.h-captcha iframe',"
                "    '.g-recaptcha iframe',"
                "    'iframe[src*=turnstile]'"
                "  ];"
                "  for (const s of selectors) {"
                "    const el = document.querySelector(s);"
                "    if (el) { el.click(); return s; }"
                "  }"
                "  return null;"
                "})()"
            )
            if result:
                logger.info("human-challenge clicked checkbox via selector {selector}", selector=result)
        except Exception as exc:
            logger.debug("human-challenge checkbox click failed: {exc}", exc=exc)

    async def _click_turnstile_widget(self, tab: Any) -> None:
        """Click the center of the Turnstile widget container if present."""
        try:
            result = await tab.evaluate(
                "(() => {"
                "  const selectors = ["
                "    '.cf-turnstile',"
                "    '#turnstile-wrapper',"
                "    '[class*=\"cf-challenge\"]',"
                "    'input[name=\"cf-turnstile-response\"]'"
                "  ];"
                "  for (const s of selectors) {"
                "    const el = document.querySelector(s);"
                "    if (!el) continue;"
                "    const rect = el.getBoundingClientRect();"
                "    const x = rect.left + rect.width / 2;"
                "    const y = rect.top + rect.height / 2;"
                "    const ev = new MouseEvent('click', {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y});"
                "    el.dispatchEvent(ev);"
                "    return s;"
                "  }"
                "  return null;"
                "})()"
            )
            if result:
                logger.info("human-challenge clicked Turnstile widget via selector {selector}", selector=result)
        except Exception as exc:
            logger.debug("human-challenge Turnstile click failed: {exc}", exc=exc)

    @staticmethod
    def choose_user_agent(index: int = 0) -> str:
        return _REALISTIC_USER_AGENTS[index % len(_REALISTIC_USER_AGENTS)]


class UnsolvedChallengeError(Exception):
    """Raised when a challenge cannot be auto-resolved within budget."""

    def __init__(self, status: ChallengeStatus) -> None:
        self.status = status
        super().__init__(f"Unsolved human challenge: {status.kind} (confidence {status.confidence:.2f})")
