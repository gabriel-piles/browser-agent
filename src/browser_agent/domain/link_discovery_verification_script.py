from __future__ import annotations

from pydantic import BaseModel, Field


class LinkDiscoveryVerificationScript(BaseModel):
    """The structured artifact the link-discovery-verification agent returns.

    A self-contained, executable async Python script that re-walks the
    target site using the main scraper's navigation/dropdown/scroll/
    lazy-load strategy and verifies that LINK DISCOVERY is complete:
    for each declared path / filter value it collects EVERY PDF link
    (running the full stable-count scroll loop, clicking any load-more
    control, iterating every dropdown option) and compares the
    discovered count to the site-advertised total — flagging paths
    where the main scraper under-collected (e.g. stopped at page one).
    It does NOT download PDFs and does NOT write to ``metadata.db``.
    """

    explanation: str = Field(
        description=(
            "Step-by-step breakdown of how the verification script "
            "re-walks the site: the PDF link selector, the dropdown/"
            "scroll/load-more/lazy-load strategy, how every declared "
            "path is iterated, how discovered counts are compared to "
            "site-advertised totals, and that validation passed."
        ),
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description=(
            "Pip packages required to run the verification script. List only non-stdlib modules the script actually imports."
        ),
    )
    python_code: str = Field(
        description=(
            "A completely self-contained, executable async Python script "
            "using zendriver and asyncio.run(). Re-discovers every PDF "
            "link per declared path (handling navigation, dropdown menus, "
            "scrolling, lazy loading) and reports per-path discovered vs "
            "advertised counts, flagging under-collection. Must not import "
            "from this project; helper imports come only from "
            "``script_tools.*``; runnable standalone with ``python <file>``."
        ),
    )

    def line_count(self) -> int:
        """Return the number of non-blank lines in ``python_code``."""
        return sum(1 for line in self.python_code.splitlines() if line.strip())
