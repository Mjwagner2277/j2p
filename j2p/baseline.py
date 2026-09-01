"""Baseline comparison and audit item creation."""

from __future__ import annotations

from typing import Any, Dict, List

from .config import lowered
from .formatting import format_number
from .models import AuditItem, PlanEpic, PlanSummary, ProjectTaskSnapshot


def compare_with_baseline(
    epics: Dict[str, PlanEpic],
    summaries: Dict[str, PlanSummary],
    baseline: Dict[str, ProjectTaskSnapshot],
    config: Dict[str, Any],
    audit: List[AuditItem],
) -> None:
    if not baseline:
        for epic in epics.values():
            add_added_epic_audit(
                audit,
                epic,
                "Epic is included in the planned sandbox and was not found in the comparison baseline.",
                config,
            )
        return

    for epic in epics.values():
        existing = baseline.get(epic.key)
        if not existing:
            add_added_epic_audit(audit, epic, "Epic is new relative to the comparison baseline.", config)
            continue

        compare_field(audit, epic, "Name", existing.name, epic.summary, "ChangedName")
        compare_field(audit, epic, "Rollup Key", existing.rollup_key, epic.rollup_key, "RollupMove")
        compare_field(audit, epic, "Resource Group", existing.resource_group, epic.resource_group, "ChangedField")
        compare_field(
            audit,
            epic,
            "% Complete",
            str(existing.percent_complete),
            str(epic.percent_complete),
            "ChangedField",
        )
        compare_field(
            audit,
            epic,
            "Total Story Points",
            format_number(existing.total_story_points),
            format_number(epic.total_story_points),
            "ChangedField",
        )
        compare_field(
            audit,
            epic,
            "Completed Story Points",
            format_number(existing.completed_story_points),
            format_number(epic.completed_story_points),
            "ChangedField",
        )
        compare_field(
            audit,
            epic,
            "Logged Hours",
            format_number(existing.logged_hours),
            format_number(epic.logged_hours),
            "ChangedField",
        )
        compare_field(
            audit,
            epic,
            story_point_ratio_field_name(config),
            format_number(existing.story_point_ratio),
            format_number(epic.story_point_ratio),
            "ChangedField",
        )
        compare_field(audit, epic, "Jira Target Start", existing.target_start, epic.target_start, "ChangedField")
        compare_field(audit, epic, "Jira Target End", existing.target_end, epic.target_end, "ChangedField")
        compare_field(
            audit,
            epic,
            "Predecessors",
            ",".join(existing.predecessors),
            ",".join(epic.predecessors),
            "DependencyChange",
        )

        was_done = existing.status.strip().lower() in lowered(config.get("done_statuses", []))
        if epic.completed and not was_done:
            audit.append(
                AuditItem(
                    "Info",
                    "CompletedSinceLastUpdate",
                    jira_key=epic.jira_key or epic.key,
                    schedule_key=epic.key,
                    issue_type="Epic",
                    summary=epic.summary,
                    field="Status",
                    old_value=existing.status,
                    new_value=epic.status,
                    color="changed_cell",
                    message="Epic is now marked done in Jira.",
                    reviewer_action="Confirm completed epic can remain inactive/hidden in the sandbox.",
                    source_row=epic.source_row,
                )
            )

    planned_keys = set(epics)
    summary_keys = {summary.key for summary in summaries.values()} | set(summaries)
    for key, existing in sorted(baseline.items()):
        if key in planned_keys or key in summary_keys:
            continue
        if existing.is_summary:
            continue
        audit.append(
            AuditItem(
                "Warning",
                "UnmatchedProjectTask",
                jira_key=existing.jira_key or key,
                schedule_key=key,
                issue_type=existing.issue_type,
                summary=existing.name,
                field="Unmatched Project Task",
                new_value="Yes",
                color="review_needed",
                message="Project task exists in the comparison baseline but is not included in the Jira CSV plan.",
                reviewer_action="Confirm whether the task should remain in the source-of-truth schedule.",
            )
        )


def add_added_epic_audit(
    audit: List[AuditItem],
    epic: PlanEpic,
    message: str,
    config: Dict[str, Any],
) -> None:
    fields = [
        ("Schedule Key", "" if epic.key == (epic.jira_key or epic.key) else epic.key),
        ("Name", epic.summary),
        ("Rollup Key", epic.rollup_key),
        ("Resource Group", epic.resource_group),
        ("Row Role", "" if epic.row_role == "Scheduled" else epic.row_role),
        ("Fix Version", epic.fix_version),
        ("Drives Schedule", "No" if not epic.drives_schedule else ""),
        ("Primary Schedule Key", "" if epic.primary_schedule_key == epic.key else epic.primary_schedule_key),
        ("% Complete", str(epic.percent_complete)),
        ("Total Story Points", format_number(epic.total_story_points)),
        ("Completed Story Points", format_number(epic.completed_story_points)),
        ("Logged Hours", format_number(epic.logged_hours)),
        (story_point_ratio_field_name(config), format_number(epic.story_point_ratio)),
        ("Jira Target Start", epic.target_start),
        ("Jira Target End", epic.target_end),
        ("Predecessors", ",".join(epic.predecessors)),
        ("Dependency Review", epic.dependency_review),
    ]
    for field_name, value in fields:
        if value == "":
            continue
        audit.append(
            AuditItem(
                "Info",
                "AddedEpic",
                jira_key=epic.jira_key or epic.key,
                schedule_key=epic.key,
                issue_type="Epic",
                summary=epic.summary,
                field=field_name,
                new_value=value,
                color="dependency_review" if field_name == "Dependency Review" else "changed_cell",
                message=message,
                reviewer_action="Review the added epic in the sandbox Project file.",
                source_row=epic.source_row,
            )
        )


def compare_field(
    audit: List[AuditItem],
    epic: PlanEpic,
    field_name: str,
    old: Any,
    new: Any,
    category: str,
    color: str = "changed_cell",
) -> None:
    old_value = "" if old is None else str(old)
    new_value = "" if new is None else str(new)
    if old_value == new_value:
        return
    severity = "Review" if category in {"ChangedName", "RollupMove"} else "Info"
    audit.append(
        AuditItem(
            severity,
            category,
            jira_key=epic.jira_key or epic.key,
            schedule_key=epic.key,
            issue_type="Epic",
            summary=epic.summary,
            field=field_name,
            old_value=old_value,
            new_value=new_value,
            color=color,
            message=f"{field_name} changed from '{old_value}' to '{new_value}'.",
            reviewer_action="Review the changed sandbox cell.",
            source_row=epic.source_row,
        )
    )


def story_point_ratio_field_name(config: Dict[str, Any]) -> str:
    return str(
        config.get("project_field_names", {}).get(
            "story_point_ratio",
            "Story Point Ratio",
        )
    )
