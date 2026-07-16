"""The per-row outcome of running :class:`SyncToUwaziUseCase`.

The :class:`ApplyResult` is what ``step_3_upload_to_uwazi.py`` prints at the
end of a run. Per-language counts give the operator a quick
sanity check; ``skip_reasons`` and ``errors`` surface the rows the
apply pipeline refused (or failed) to push, with a reason
inspectable from the logs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApplyResult(BaseModel):
    """The outcome of pushing a :class:`SyncPlan` to Uwazi."""

    model_config = ConfigDict(extra="forbid")

    per_language_counts: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="language -> action -> count.",
    )
    skip_reasons: tuple[tuple[str, str, str], ...] = Field(
        default_factory=tuple,
        description="(language, source_url, reason) tuples for rows the apply pipeline refused.",
    )
    error_rows: tuple[tuple[str, str, str], ...] = Field(
        default_factory=tuple,
        description="(language, source_url, message) tuples for rows that failed mid-flight.",
    )

    def per_language(self) -> dict[str, dict[str, int]]:
        """Return the per-language action counts (alias for the field)."""
        return self.per_language_counts

    def skip_reasons_list(self) -> list[tuple[str, str, str]]:
        """Return the skip reasons as a list (tier 3+ compat)."""
        return list(self.skip_reasons)

    def errors(self) -> list[tuple[str, str, str]]:
        """Return the error rows as a list (tier 3+ compat)."""
        return list(self.error_rows)
