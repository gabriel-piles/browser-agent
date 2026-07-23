"""Model for human/anti-bot challenge detection results."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChallengeStatus(BaseModel):
    """Result of inspecting a page for anti-bot challenges.

    ``kind`` is one of: ``none``, ``cloudflare_turnstile``,
    ``recaptcha``, ``hcaptcha``, ``cloudflare_iuam``,
    ``generic_wait``, ``unknown``.

    ``confidence`` is a float 0..1 derived from how many heuristics
    fired. ``details`` explains which indicators matched.
    """

    model_config = ConfigDict(frozen=True)

    is_challenge: bool = False
    kind: str = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    details: list[str] = Field(default_factory=list)

    @classmethod
    def none(cls) -> ChallengeStatus:
        """Return a no-challenge status sentinel."""
        return cls()

    def __bool__(self) -> bool:
        return self.is_challenge
