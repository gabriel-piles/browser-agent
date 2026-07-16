"""Print the upload-validation report for the match driver.

Hides the existing-entity / plan-counts / per-row issues
report behind one object. The match driver calls
:meth:`print_report` once after the row classifier has
finished walking the run's metadata.db rows.
"""

from __future__ import annotations


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
        label = "entity" if existing_count == 1 else "entities"
        print(f"\n  Upload validation for template {mapping.template!r}:")
        print(f"    {existing_count} existing {label} on Uwazi")
        print(
            "    plan: "
            f"create={counts.get('create', 0)}, "
            f"update={counts.get('update', 0)}, "
            f"skip={counts.get('skip', 0)}"
        )
        if not issues:
            print("    no issues found in new entities")
            return
        print(f"    {len(issues)} new row(s) have issues:")
        for source_url, title, row_issues in issues:
            heading = f"      - {title!r} ({source_url})" if title else f"      - ({source_url})"
            print(heading)
            for issue in row_issues:
                print(f"          * {issue}")
