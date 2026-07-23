"""Bundled data the match driver passes between its stages.

Holds the loaded :class:`UwaziMapping`, the live
:class:`UwaziTemplate` + ``thesaurus_id -> ThesauriSnapshot``
map, the per-field value :class:`Counter` map from
``metadata.db``, and the live :class:`UwaziClient`. Lives
in its own file so the match-context loader stays focused
on the loading logic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from browser_agent.domain.thesauri_snapshot import ThesauriSnapshot
from browser_agent.domain.uwazi_mapping import UwaziMapping
from browser_agent.domain.uwazi_template import UwaziTemplate
from uwazi_api.client import UwaziClient


@dataclass
class MatchContext:
    """Bundled data the match driver passes between its stages."""

    mapping: UwaziMapping
    template: UwaziTemplate
    thesauri_by_id: dict[str, ThesauriSnapshot]
    relationships_by_id: dict[str, ThesauriSnapshot]
    field_counters: dict[str, Counter]
    client: UwaziClient
