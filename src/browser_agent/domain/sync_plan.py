"""One row in a :class:`SyncPlan`."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from browser_agent.domain.uwazi_mapping import UwaziMapping


class SyncAction(str, Enum):
    """The action :class:`SyncPlan` will take for one row."""

    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


class SyncPlanRow(BaseModel):
    """One row in the :class:`SyncPlan`, ready to push to Uwazi."""

    model_config = ConfigDict(extra="forbid")

    action: SyncAction
    language: str = Field(description="ISO language code; ``default_language`` when the mapping does not pin one.")
    source_url: str = Field(description="Original source URL — used as the natural key when no explicit key is set.")
    title: str = Field(description="Entity title; becomes ``title`` on Uwazi.")
    metadata: dict = Field(default_factory=dict, description="Property name -> value dict, post-thesaurus-substitution.")
    pdf_path: str | None = Field(default=None, description="Local PDF path, if any, to upload as the primary file.")
    key_value: str | None = Field(
        default=None, description="The key used to find/create the entity (e.g. URL path placeholder value)."
    )
    mapping_sha256: str = Field(default="", description="Mapping SHA at plan time, for drift detection.")


class SyncPlan(BaseModel):
    """The full plan that ``uwazi_apply`` pushes to Uwazi."""

    model_config = ConfigDict(extra="forbid")

    mapping: UwaziMapping
    rows: tuple[SyncPlanRow, ...] = Field(default_factory=tuple)

    def total_counts(self) -> dict[str, int]:
        """Return the action counts (create / update / skip)."""
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.action.value] = out.get(row.action.value, 0) + 1
        return out
