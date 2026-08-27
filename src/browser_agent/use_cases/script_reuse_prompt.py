"""Constrained script-reuse prompt for the adaptation LLM call."""

from __future__ import annotations

from browser_agent.domain.subtask_spec import SubtaskSpec

_ADAPT_PROMPT_TEMPLATE = """\
You are adapting a PROVEN scraping script for a sibling subtask on the \
same site family. The source script already works end-to-end.

SOURCE SCRIPT (working, do not restructure):
```python
{source_code}
```

TARGET SUBTASK: {subtask_id} ({kind})
Description: {description}

Target verified selectors (from planning):
{selectors}

Target filter labels: {filter_labels}
Target sample document URLs:
{sample_urls}

Adaptation rules:
- Change ONLY constants: filter labels (e.g. FILTER_LABELS), target \
URLs, session/date ranges, and any target-specific configuration.
- The script's structure, selectors, waits, download mechanics, and \
record-saving logic MUST stay exactly as in the source.
- If the target's verified selectors or page types are fundamentally \
different from what the source script handles (not just different \
labels/URLs), you MUST NOT adapt. Reply with status "incompatible" \
and explain the mismatch in `explanation`.
- Keep the source script's pdf_download_strategy and dependencies.\
"""

_INCOMPATIBLE_NOTE = (
    "The source script's page type or mechanics cannot satisfy this subtask; a from-scratch build is required."
)


class ScriptReusePrompt:
    """Render the constrained adapt-or-reject prompt."""

    @staticmethod
    def render(subtask: SubtaskSpec, source_code: str) -> str:
        return _ADAPT_PROMPT_TEMPLATE.format(
            source_code=source_code,
            subtask_id=subtask.subtask_id,
            kind=subtask.kind,
            description=subtask.description,
            selectors="\n".join(f"- {s}" for s in subtask.verified_selectors) or "- (none)",
            filter_labels=", ".join(subtask.filter_labels) or "(none)",
            sample_urls="\n".join(f"- {u}" for u in subtask.sample_document_urls[:5]) or "- (none)",
        )

    @staticmethod
    def incompatible_note() -> str:
        """Reason text recorded when the adapter rejects the source."""
        return _INCOMPATIBLE_NOTE
