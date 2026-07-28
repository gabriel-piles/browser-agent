from __future__ import annotations

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    """A single named run definition from ``data/prompts/<name>.yaml``.

    Carries the ``name`` used to create the per-run folder under
    ``data/runs/<name>/`` and the natural-language ``prompt`` that
    drives the script-generation agent. The Uwazi-mapping knobs
    (``template``, ``max_fields_in_prompt``, ``examples_per_field``,
    ``push``, ``run_filter``) are optional and read by the three
    ``uwazi_*`` drivers; a run that never maps to Uwazi can omit them.
    """

    name: str = Field(min_length=1, description="Unique run name; becomes the folder name under data/runs/.")
    prompt: str = Field(min_length=1, description="The natural-language task description for this run.")
    template: str | None = Field(
        default=None,
        description="Name of the Uwazi template to map onto; read by uwazi_propose/uwazi_match/uwazi_apply.",
    )
    max_fields_in_prompt: int = Field(
        default=60,
        description="Cap on how many catalog fields ship in the LLM propose prompt.",
    )
    examples_per_field: int = Field(
        default=5,
        description="Cap on how many sample values per field are summarised for the LLM.",
    )
    push: bool = Field(
        default=True,
        description="When False, uwazi_apply builds the plan but does not mutate the remote instance.",
    )
    run_filter: str | None = Field(
        default=None,
        description=(
            "Optional task_slug to filter metadata.db rows by when building an "
            "upload plan. When set, only rows with this task_slug are pushed. "
            "When None, every row in the metadata db is included."
        ),
    )
    parallel_runners: int | None = Field(
        default=None,
        ge=1,
        le=8,
        description=(
            "Number of concurrent browser tabs the emitted script should use "
            "for the per-document phase. None (the default) keeps the classic "
            "single-tab flow; an int >= 2 instructs the agent to fan the "
            "document loop out across that many tabs gated by an "
            "asyncio.Semaphore. Capped at 8 to stay within the site's tolerance."
        ),
    )
