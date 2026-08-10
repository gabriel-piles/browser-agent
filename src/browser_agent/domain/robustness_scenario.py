from __future__ import annotations

from pydantic import BaseModel, Field

from browser_agent.domain.expected_output import ExpectedOutput


class RobustnessScenario(BaseModel):
    """A single robustness test scenario for the loop.

    Each scenario is a fixture-backed local site pattern the
    generation pipeline must produce a working script for. The
    ``prompt`` becomes the run YAML prompt; ``expected`` drives the
    verification harness.
    """

    name: str = Field(
        description="Scenario slug, e.g. 'single_page_list'.",
    )
    difficulty: int = Field(
        ge=1,
        le=8,
        description="Difficulty level 1-8 controlling escalation order.",
    )
    pattern: str = Field(
        description="What site pattern it tests, e.g. 'infinite_scroll'.",
    )
    prompt: str = Field(
        description="The natural-language scraping task (becomes the run YAML prompt).",
    )
    fixture_dir: str = Field(
        description="Relative path to the fixture directory under scripts/fixtures/.",
    )
    expected: ExpectedOutput = Field(
        description="What the emitted script should produce.",
    )
    description: str = Field(
        default="",
        description="Human-readable explanation of what this scenario probes.",
    )
