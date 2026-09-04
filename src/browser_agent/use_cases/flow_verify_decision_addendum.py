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
- ``re_execute`` — the emitted script is CORRECT and already enumerates the
  gap; the missing files are due to an interrupted or incomplete execution
  (a backfill/repair run that died mid-way, a bounded run that stopped
  early), not a logic bug. ``focus`` = the concrete re-run instruction
  (which sessions/paths to re-run, or "re-run the default run to
  completion"). Do NOT choose this when the script has a selector or
  navigation bug — that is ``rewrite_script``. Do NOT choose
  ``rewrite_script`` when the only fix is to re-run the already-correct
  script to completion.
- ``accept`` — the remaining documents are NOT AVAILABLE on the site
  (404s, empty sessions, removed pages) or the gap is too small to
  justify investing another build/verify cycle (e.g. a handful of
  missing variants out of thousands, all optional formats). ``focus`` =
  why the gap is acceptable.

When the prompt carries a **Previous verification round** block, treat it as your own earlier verdict: if the gap is unchanged and the evidence (deterministic reconciler inventory + execution summary) confirms the same cause, choose ``accept`` — another identical rewrite cannot change what the site itself does not serve. The **Execution evidence** block is the script's own deterministic log; cite it instead of re-deriving row counts with `query_db`/`run_read_script`.

Be decisive with the site's evidence: probe the missing paths live
before requesting a rewrite — do NOT ask for a rewrite when the site
itself does not serve the documents. Do NOT request ``add_extra_script``
when the current script can plausibly cover the gap with a small fix;
that is a ``rewrite_script``. When every declared path the site actually
serves is covered and only unavailable documents remain, choose
``accept``.

Judge coverage ONLY against THIS SPLIT's scope as stated first in the task
prompt. The original task after it is context; sessions/areas the chunk
description does not own are other splits' work and must NEVER be reported
as missing coverage or trigger rewrite_script/add_extra_script.
""".strip()
