"""Print the human-facing plan + apply summaries for the apply driver.

Hides the ``Plan rows:``, ``Plan counts:``, and per-language
action counts (with skip / error rows) behind one object so
the driver stays a thin orchestrator.
"""

from __future__ import annotations


class ApplyResultPrinter:
    """Print the plan row count, plan action counts, and the apply result summary."""

    def print_plan_rows(self, plan) -> None:
        """Print the total number of rows the plan will consider."""
        print(f"Plan rows: {len(plan.rows)}")

    def print_plan_counts(self, plan) -> None:
        """Print the per-action counts (create / update / skip) of the plan."""
        counts = plan.total_counts()
        print(f"Plan counts: {counts}")

    def print_apply_result(self, result) -> None:
        """Print the per-language action counts, skip rows, and errors."""
        print("\nApply result:")
        for language, counts in result.per_language_counts.items():
            print(f"  {language}: {counts}")
        self._print_skips(result)
        self._print_errors(result)

    def _print_skips(self, result) -> None:
        """Print every skip-reason row, plus a per-reason summary count.

        Without the summary the operator only sees the first five
        rows; with a mix of ``no_local_pdf`` and ``already_on_uwazi``
        the rest is invisible. The summary line is the cheapest way
        to make the trade-off (skip-by-pdf vs skip-by-duplicate)
        scannable in a few characters.
        """
        skip_rows = list(result.skip_reasons)
        if not skip_rows:
            return
        print(f"  skips: {len(skip_rows)}")
        for language, source_url, reason in skip_rows:
            print(f"    - {language} {source_url}: {reason}")
        counts: dict[str, int] = {}
        for _language, _source_url, reason in skip_rows:
            counts[reason] = counts.get(reason, 0) + 1
        summary = ", ".join(f"{reason}={n}" for reason, n in sorted(counts.items()))
        print(f"  skip reasons: {summary}")

    def _print_errors(self, result) -> None:
        """Print the per-row errors when the result has any."""
        error_rows = list(result.error_rows)
        if not error_rows:
            return
        print(f"  errors: {len(error_rows)}")
        for language, source_url, message in error_rows:
            print(f"    - {language} {source_url}: {message}")
