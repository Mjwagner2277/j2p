"""Represent initiative estimates only where exported child scope leaves a remainder."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Sequence

from .formatting import format_number
from .jira import jira_key_prefix
from .metrics import select_story_points
from .models import AuditItem, JiraIssue, PlanEpic, RollupAssignment
from .rollups import accepted_fix_versions, resolve_rollup_assignments


def add_initiative_estimates(
    planned_epics: Dict[str, PlanEpic],
    initiatives: Mapping[str, JiraIssue],
    epics: Sequence[JiraIssue],
    stories_by_epic: Mapping[str, Sequence[JiraIssue]],
    config: Dict[str, Any],
    audit: List[AuditItem],
) -> None:
    """Add remaining parent estimates without counting exported epic work twice.

    Deduction uses the entire exported child hierarchy, including epics outside
    the configured schedule scope. Filtering a child must not turn its existing
    estimate into additional parent work in an unrelated accepted fixVersion.
    """
    children_by_initiative: Dict[str, List[JiraIssue]] = {}
    for epic in epics:
        if epic.parent:
            children_by_initiative.setdefault(epic.parent.upper(), []).append(epic)

    done_statuses = {status.strip().casefold() for status in config.get("done_statuses", [])}
    for initiative in initiatives.values():
        child_epics = children_by_initiative.get(initiative.key.upper(), [])
        child_total = 0.0
        for epic in child_epics:
            children = stories_by_epic.get(epic.key.upper(), [])
            current_points = sum(child.story_points or 0.0 for child in children)
            effective_points, _ = select_story_points(epic, current_points, len(children), config)
            child_total += effective_points
        child_total = round(child_total, 2)
        selected_total, _ = select_story_points(initiative, child_total, len(child_epics), config)
        remainder = round(max(selected_total - child_total, 0.0), 2)
        if remainder <= 0:
            continue

        prefix = jira_key_prefix(initiative.key)
        resource_group = config.get("resource_groups", {}).get(prefix)
        rollup_mode = config.get("rollup_modes", {}).get(prefix)
        if not resource_group or rollup_mode not in {"initiative", "fixVersion"}:
            _audit(audit, initiative, "ExcludedInitiativeEstimateResource",
                   f"Remaining initiative estimate of {format_number(remainder)} points was excluded: "
                   f"prefix '{prefix}' has no configured resource group and rollup mode.",
                   field="Resource Group", old_value=prefix)
            continue

        estimate_key = initiative.key + "::ESTIMATE"
        if rollup_mode == "initiative":
            assignments = [RollupAssignment(
                schedule_key=estimate_key,
                rollup_key=initiative.key,
                rollup_name=initiative.summary or initiative.key,
                row_role="Estimate",
                fix_version="",
                drives_schedule=True,
                primary_schedule_key=estimate_key,
            )]
        else:
            accepted_versions = accepted_fix_versions(initiative, config)
            ignored_versions = [name for name in initiative.fix_versions if name not in accepted_versions]
            if not accepted_versions:
                _audit(audit, initiative, "ExcludedInitiativeEstimateScope",
                       f"Remaining initiative estimate of {format_number(remainder)} points was excluded: "
                       "the initiative has no accepted fixVersion of its own. Child epic versions are not inherited.",
                       field="Fix versions", old_value=", ".join(initiative.fix_versions))
                continue
            if ignored_versions:
                _audit(audit, initiative, "IgnoredInitiativeEstimateFixVersion",
                       "Remaining initiative estimate is included only under its accepted fixVersions.",
                       field="Fix versions", old_value=", ".join(ignored_versions),
                       new_value=", ".join(accepted_versions))
            assignments, error = resolve_rollup_assignments(
                replace(initiative, key=estimate_key), rollup_mode, initiatives, config, prefix,
            )
            if error:
                _audit(audit, initiative, "ExcludedInitiativeEstimateScope", error, field="Rollup")
                continue

        for assignment in assignments:
            planned_epics[assignment.schedule_key] = PlanEpic(
                key=assignment.schedule_key,
                jira_key=initiative.key,
                issue_id=initiative.issue_id,
                issue_type="Initiative",
                estimate_only=True,
                summary=(initiative.summary or initiative.key) + " — Remaining estimate",
                status=initiative.status,
                rollup_mode=rollup_mode,
                rollup_key=assignment.rollup_key,
                rollup_name=assignment.rollup_name,
                resource_group=resource_group,
                key_prefix=prefix,
                total_story_points=remainder,
                completed_story_points=0.0,
                logged_hours=0.0,
                completed_logged_hours=0.0,
                story_point_ratio=0.0,
                percent_complete=0,
                in_planning=False,
                completed=initiative.status.strip().casefold() in done_statuses,
                target_start=initiative.target_start,
                target_end=initiative.target_end,
                row_role="Estimate" if assignment.drives_schedule else "Reference",
                fix_version=assignment.fix_version,
                drives_schedule=assignment.drives_schedule,
                primary_schedule_key=assignment.primary_schedule_key,
                source_row=initiative.source_row,
                source_file=initiative.source_file,
                original_story_points=initiative.original_story_points,
                child_story_points=child_total,
                story_point_basis="Remaining initiative estimate",
            )
        _audit(
            audit, initiative, "InitiativeEstimate",
            "Remaining initiative estimate: "
            f"original={format_number(initiative.original_story_points or 0.0)}, "
            f"exported child epics={format_number(child_total)}, "
            f"selected total={format_number(selected_total)}, "
            f"remaining estimate={format_number(remainder)}. "
            "Child scope is deducted before schedule filtering; remaining points do not count as completed work. "
            "This estimate row does not represent the whole initiative's dependency graph.",
            field="Original Story Points", new_value=format_number(remainder),
            schedule_key=estimate_key,
        )


def _audit(audit: List[AuditItem], issue: JiraIssue, category: str, message: str, **fields: str) -> None:
    audit.append(AuditItem(
        "Info", category, jira_key=issue.key, issue_type=issue.issue_type,
        summary=issue.summary, message=message, source_row=issue.source_row,
        source_file=issue.source_file, **fields,
    ))
