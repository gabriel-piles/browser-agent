"""Build the user + system prompt for one thesaurus-matching LLM call.

Hides the static rules block + the thesaurus allowlist bullet
list + the extracted-value line list behind one object. The
match driver calls :meth:`build` for every thesaurus.
"""

from __future__ import annotations

# Static text block embedded into every LLM prompt for one thesaurus.
_PROMPT_RULES: tuple[str, ...] = (
    "",
    "For EACH extracted value, choose the single best matching allowed thesaurus value. Rules:",
    "- If an exact or near-exact match exists (ignoring accents/case), use it.",
    "- If no plausible match exists, output uwazi_value: null.",
    "- If you are uncertain, still pick the closest and set needs_review: true; otherwise needs_review: false.",
    "",
    "Output ONLY a YAML list, one entry per extracted value:",
    '- crawl_value: "..."',
    '  uwazi_value: "..."',
    "  needs_review: false",
)


class ThesaurusMatchPromptBuilder:
    """Build the (user prompt, system prompt) pair for one thesaurus LLM call."""

    def build(
        self,
        thesaurus_name: str,
        thesaurus_values: tuple[str, ...],
        remaining: list[tuple[str, int]],
    ) -> str:
        """Return the user-turn prompt for the thesaurus matching call."""
        head = [f'The Uwazi thesaurus "{thesaurus_name}" contains exactly these allowed values:']
        body = ["", "Below are distinct values extracted from the metadata.db:"]
        return "\n".join(
            head + self._thesaurus_lines(thesaurus_values) + body + self._value_lines(remaining) + list(_PROMPT_RULES)
        )

    def system_prompt(self) -> str:
        """Return the system prompt the LLM agent uses for every thesaurus call."""
        return (
            "You are a data normalization assistant. "
            "You map free-text values extracted from a website to the "
            "controlled vocabulary (thesaurus) of a Uwazi database. "
            "Output ONLY valid YAML."
        )

    def _thesaurus_lines(self, values) -> list[str]:
        """Render the thesaurus allowlist as a sorted bullet list."""
        return [f"- {tv}" for tv in sorted(values)]

    def _value_lines(self, remaining: list[tuple[str, int]]) -> list[str]:
        """Format the per-value lines (with occurrence counts) for the prompt."""
        return [f'- "{val}" ({count} occurrences)' for val, count in remaining]
