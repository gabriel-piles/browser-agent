from __future__ import annotations

from pydantic import BaseModel


class SmokeTestResult(BaseModel):
    """Outcome of running the emitted script as a subprocess."""

    success: bool
    output: str
    timed_out: bool

    def to_payload(self) -> dict[str, object]:
        """Return a JSON-serializable dict for sidecar persistence."""
        return {"success": self.success, "timed_out": self.timed_out, "output": self.output}
