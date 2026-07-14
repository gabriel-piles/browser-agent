"""Model for human/anti-bot challenge detection results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChallengeStatus:
    """Result of inspecting a page for anti-bot challenges.

    ``kind`` is one of: ``none``, ``cloudflare_turnstile``,
    ``recaptcha``, ``hcaptcha``, ``cloudflare_iuam``,
    ``generic_wait``, ``unknown``.

    ``confidence`` is a float 0..1 derived from how many heuristics
    fired. ``details`` explains which indicators matched.
    """

    is_challenge: bool
    kind: str
    confidence: float
    details: list[str]

    @classmethod
    def none(cls) -> "ChallengeStatus":
        return cls(is_challenge=False, kind="none", confidence=0.0, details=[])

    def __bool__(self) -> bool:
        return self.is_challenge
