"""System prompt additions for the flow verify agent's decision contract."""

from __future__ import annotations

# Appended to the legacy VERIFICATION_SYSTEM_PROMPT for the flow's verify
# agent: after the coverage analysis, the agent must also decide what
# happens next for this subtask — repair the script, add an extra script
# for uncovered paths, or accept the gap as good enough.
FLOW_VERIFY_DECISION_ADDENDUM = """

## Flow decision (REQUIRED in your output)

This subtask's script was verified as part of a split flow. Besides the
coverage analysis above, your output MUST carry a ``decision`` object
with ``action``, ``focus``, and ``reasoning``. Choose exactly one:

- ``rewrite_script`` — the emitted script has a FIXABLE logic bug (wrong
  selector, missing filter iteration, pagination stopped early, download
  helper not called) and the gap is large enough to justify another
  build/verify cycle. ``focus`` = the concrete fix instruction handed to
  the writer (what to change, which path/selectors).
- ``add_extra_script`` — the uncovered paths need mechanics the current
  script cannot adopt (a genuinely different page family, a different
  host/container, a flow the current script would have to be rewritten
  around). ``focus`` = exactly which paths/URLs the EXTRA script must
  cover and the mechanics it needs; a NEW separate script will be built
  for them.
- ``accept`` — the remaining documents are NOT AVAILABLE on the site
  (404s, empty sessions, removed pages) or the gap is too small to
  justify investing another build/verify cycle (e.g. a handful of
  missing variants out of thousands, all optional formats). ``focus`` =
  why the gap is acceptable.

Be decisive with the site's evidence: probe the missing paths live
before requesting a rewrite — do NOT ask for a rewrite when the site
itself does not serve the documents. Do NOT request ``add_extra_script``
when the current script can plausibly cover the gap with a small fix;
that is a ``rewrite_script``. When every declared path the site actually
serves is covered and only unavailable documents remain, choose
``accept``.
""".strip()
