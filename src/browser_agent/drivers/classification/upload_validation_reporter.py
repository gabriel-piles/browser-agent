"""Print the upload-validation report for the match driver.

Hides the existing-entity / plan-counts / per-row issues
report behind one object. The match driver calls
:meth:`print_report` once after the row classifier has
finished walking the run's metadata.db rows.
"""

from __future__ import annotations

from browser_agent.drivers.console.section_printer import SectionPrinter


class UploadValidationReporter:
    """Print the upload-validation report: existing-entity count, plan counts, issues."""

    def print_report(
        self,
        mapping,
        counts: dict[str, int],
        issues: list[tuple[str, str, list[str]]],
        existing_count: int,
    ) -> None:
        """Print the upload-validation report for ``mapping``'s template."""
        SectionPrinter().heading("Upload validation")
        print(f"  template:  {mapping.template!r}")
        label = "entity" if existing_count == 1 else "entities"
        print(f"  existing:  {existing_count} {label} on Uwazi")
        print(f"  plan: create={counts.get('create', 0)}, update={counts.get('update', 0)}, skip={counts.get('skip', 0)}")
        if not issues:
            print("  no issues found in new entities")
            return
        print(f"  {len(issues)} new row(s) have issues:")
        for source_url, title, row_issues in issues:
            line = f"    - {title!r} ({source_url})" if title else f"    - ({source_url})"
            print(line)
            for issue in row_issues:
                print(f"        * {issue}")
