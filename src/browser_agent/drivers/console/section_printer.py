"""Print section headings to delimit driver output phases.

Keeps the heading-rule style behind one object so every reporter
in the match/validate pipeline emits visually consistent section
boundaries. The match driver and its collaborators call
:meth:`SectionPrinter.heading` at the start of each output
block (mapping loaded, thesauri loaded, default values, upload
validation, thesaurus matching) so the human reading the log can
tell where one phase ends and the next begins.
"""

from __future__ import annotations

# Rule width matches a typical 80-col terminal; plain ASCII so the
# line stays identical in every terminal and every log capture.
_RULE = "-" * 60
_BLANK = ""


class SectionPrinter:
    """Print a heading framed by horizontal rules and a blank line."""

    def heading(self, title: str) -> None:
        """Print ``title`` framed by two rules and a trailing blank line."""
        print(_BLANK)
        print(_RULE)
        print(f" {title}")
        print(_RULE)

    def subheading(self, title: str) -> None:
        """Print a lighter ``title`` line for a nested block under a heading."""
        print(f"\n {title}")
