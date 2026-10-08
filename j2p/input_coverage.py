"""Project coverage derived from source issues before schedule filtering."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

from .jira import jira_key_prefix
from .models import AuditItem, JiraIssue


def project_coverage(issues: Iterable[JiraIssue], config: Dict[str, Any]) -> Dict[str, Any]:
    """Compare configured projects with all unique source issue keys.

    A project is present even when it contains only child issues or every epic
    is outside the accepted fixVersion scope. File names and planned rows do
    not establish source coverage. Overlapping exports count each key once.
    """
    configured = {
        str(prefix).strip().upper()
        for prefix in config.get("resource_groups", {})
        if str(prefix).strip()
    }
    keys = {str(issue.key).strip().upper() for issue in issues if issue.key and str(issue.key).strip()}
    counts = Counter(jira_key_prefix(key) for key in keys)
    present = set(counts)
    return {
        "configured_projects": sorted(configured),
        "input_projects": sorted(present),
        "missing_projects": sorted(configured - present),
        "unconfigured_projects": sorted(present - configured),
        "issue_counts_by_project": {prefix: counts[prefix] for prefix in sorted(counts)},
    }


def coverage_warnings(
    coverage: Dict[str, Any],
    source_label: str,
    warn_missing: bool = True,
    warn_unconfigured: bool = True,
) -> List[AuditItem]:
    """Return project-level warnings without printing or inventing Jira keys."""
    warnings: List[AuditItem] = []
    source = str(source_label or "selected CSV exports")
    if warn_missing:
        for prefix in sorted(set(coverage.get("missing_projects", []))):
            warnings.append(AuditItem(
                "Warning", "MissingProjectExport", summary=f"Missing project export: {prefix}",
                field="Jira Project", old_value=prefix, source_file=str(source_label or ""),
                message=(
                    f"Configured Jira project {prefix} has no issue rows in {source}. "
                    "Totals and cross-project links may be incomplete."
                ),
                reviewer_action=(
                    f"Add an export containing {prefix} issues, or confirm that this partial scope is intentional."
                ),
            ))
    if warn_unconfigured:
        for prefix in sorted(set(coverage.get("unconfigured_projects", []))):
            warnings.append(AuditItem(
                "Warning", "UnconfiguredJiraProject", summary=f"Unconfigured Jira project: {prefix}",
                field="Jira Project", old_value=prefix, source_file=str(source_label or ""),
                message=(
                    f"Jira project {prefix} appears in {source} but is not included in resource_groups. "
                    "Its epics and initiative estimates are excluded from the schedule; "
                    "linked child issues can still contribute to configured epics."
                ),
                reviewer_action=(
                    f"Add {prefix} to resource_groups and rollup_modes, or confirm that its schedule exclusion is intentional."
                ),
            ))
    return warnings
