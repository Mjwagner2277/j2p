"""Core Jira CSV planning orchestration.

The focused implementation lives in smaller modules. This module remains the
public planning facade used by the CLI and tests.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .baseline import (
    add_added_epic_audit,
    compare_field,
    compare_with_baseline,
    story_point_ratio_field_name,
)
from .config import logical_columns, lowered
from .dependencies import add_dependency_review, apply_dependencies, creates_cycle, primary_planned_key
from .formatting import format_number, html_escape
from .jira import (
    JIRA_KEY_RE,
    CsvTable,
    jira_key_prefix,
    normalize_header,
    parse_date,
    parse_issue_keys,
    parse_issues,
    parse_logged_hours,
    parse_number,
    split_multi_values,
)
from .metrics import calculate_percent, calculate_story_point_ratio
from .models import (
    AuditItem,
    J2PError,
    JiraIssue,
    PlanEpic,
    PlanSummary,
    ProjectTaskSnapshot,
    RollupAssignment,
    RunPlan,
)
from .rollups import (
    add_multi_fixversion_audit,
    build_summaries,
    describe_rollup_modes,
    fix_version_schedule_key,
    multi_fixversion_policy_for_prefix,
    resolve_rollup_assignments,
    rollup_mode_for_prefix,
    summary_id,
)
from .state import audit_to_rows, run_plan_to_state, snapshots_from_state, write_json


def build_run_plan(
    jira_csv: Path,
    config: Dict[str, Any],
    baseline: Optional[Dict[str, ProjectTaskSnapshot]] = None,
) -> RunPlan:
    table = CsvTable(jira_csv)
    audit: List[AuditItem] = []
    baseline = baseline or {}
    required = ["jira_key", "issue_type", "summary", "epic_link", "story_points", "status"]
    configured_rollup_modes = set(config.get("rollup_modes", {}).values())
    if "initiative" in configured_rollup_modes:
        required.append("parent")
    if "fixVersion" in configured_rollup_modes:
        required.append("fix_versions")
    missing = [name for name in required if not table.has_any(logical_columns(config, name))]
    if missing:
        details = ", ".join(f"{name}: {logical_columns(config, name)}" for name in missing)
        raise J2PError(f"CSV is missing required mapped columns: {details}")

    column_map = {
        name: table.selected_header(logical_columns(config, name))
        for name in sorted(config.get("columns", {}).keys())
    }

    issues = parse_issues(table, config, audit)
    issue_type_sets = {
        "initiative": lowered(config["issue_types"]["initiative"]),
        "epic": lowered(config["issue_types"]["epic"]),
        "story": lowered(config["issue_types"]["story"]),
    }

    initiatives = {
        issue.key: issue
        for issue in issues
        if issue.issue_type.strip().lower() in issue_type_sets["initiative"]
    }
    epics = [
        issue for issue in issues if issue.issue_type.strip().lower() in issue_type_sets["epic"]
    ]
    stories = [
        issue for issue in issues if issue.issue_type.strip().lower() in issue_type_sets["story"]
    ]

    story_rollup_by_epic: Dict[str, Dict[str, float]] = {}
    done_statuses = lowered(config.get("done_statuses", []))
    for story in stories:
        if not story.epic_link:
            audit.append(
                AuditItem(
                    "Warning",
                    "StoryMissingEpicLink",
                    jira_key=story.key,
                    issue_type=story.issue_type,
                    summary=story.summary,
                    message="Story/task row has no Epic Link and cannot contribute to epic completion.",
                    reviewer_action="Confirm the Jira export includes Epic Link for child work.",
                    source_row=story.source_row,
                )
            )
            continue
        points = story.story_points or 0.0
        bucket = story_rollup_by_epic.setdefault(
            story.epic_link,
            {"total": 0.0, "completed": 0.0, "logged_hours": 0.0, "completed_logged_hours": 0.0},
        )
        bucket["total"] += points
        bucket["logged_hours"] += story.logged_hours
        if story.status.strip().lower() in done_statuses:
            bucket["completed"] += points
            bucket["completed_logged_hours"] += story.logged_hours

    planned_epics: Dict[str, PlanEpic] = {}
    excluded_count = 0
    resource_groups = config.get("resource_groups", {})
    hours_per_story_point = float(config.get("metrics", {}).get("hours_per_story_point", 8.0))
    if not resource_groups:
        audit.append(
            AuditItem(
                "Warning",
                "MissingResourceGroupConfig",
                message="No resource_groups were configured. Epics cannot be assigned to teams.",
                reviewer_action="Add Jira key prefixes to the YAML configuration.",
            )
        )

    for epic in epics:
        prefix = jira_key_prefix(epic.key)
        resource_group = resource_groups.get(prefix)
        if not resource_group:
            excluded_count += 1
            audit.append(
                AuditItem(
                    "Warning",
                    "ExcludedUnknownPrefix",
                    jira_key=epic.key,
                    issue_type=epic.issue_type,
                    summary=epic.summary,
                    field="Resource Group",
                    old_value=prefix,
                    color="review_needed",
                    message=f"Epic key prefix '{prefix}' is not mapped to a resource group.",
                    reviewer_action="Add the prefix to resource_groups or confirm the epic should be excluded.",
                    source_row=epic.source_row,
                )
            )
            continue

        rollup_mode = rollup_mode_for_prefix(config, prefix)
        assignments, rollup_error = resolve_rollup_assignments(epic, rollup_mode, initiatives, config, prefix)
        if rollup_error:
            excluded_count += 1
            audit.append(
                AuditItem(
                    "Warning",
                    "ExcludedMissingRollup",
                    jira_key=epic.key,
                    issue_type=epic.issue_type,
                    summary=epic.summary,
                    field="Rollup",
                    color="review_needed",
                    message=rollup_error,
                    reviewer_action="Add the required initiative parent or fixVersion in Jira, then rerun.",
                    source_row=epic.source_row,
                )
            )
            continue

        point_bucket = story_rollup_by_epic.get(
            epic.key,
            {"total": 0.0, "completed": 0.0, "logged_hours": 0.0, "completed_logged_hours": 0.0},
        )
        total_points = round(point_bucket["total"], 2)
        completed_points = round(point_bucket["completed"], 2)
        logged_hours = round(point_bucket["logged_hours"], 2)
        completed_logged_hours = round(point_bucket["completed_logged_hours"], 2)
        in_planning = total_points <= 0
        percent_complete = calculate_percent(completed_points, total_points)
        story_point_ratio = calculate_story_point_ratio(
            completed_logged_hours,
            completed_points,
            hours_per_story_point,
        )
        completed = epic.status.strip().lower() in done_statuses

        if in_planning:
            audit.append(
                AuditItem(
                    "Review",
                    "InPlanning",
                    jira_key=epic.key,
                    issue_type=epic.issue_type,
                    summary=epic.summary,
                    field="In Planning",
                    new_value="Yes",
                    color="in_planning",
                    message="Epic has no pointed child stories/tasks and is marked In Planning.",
                    reviewer_action="Confirm this epic is intentionally unestimated or add pointed child work.",
                    source_row=epic.source_row,
                )
            )

        for assignment in assignments:
            planned_epics[assignment.schedule_key] = PlanEpic(
                key=assignment.schedule_key,
                issue_id=epic.issue_id,
                summary=epic.summary,
                status=epic.status,
                rollup_mode=rollup_mode,
                rollup_key=assignment.rollup_key,
                rollup_name=assignment.rollup_name,
                resource_group=resource_group,
                key_prefix=prefix,
                total_story_points=total_points,
                completed_story_points=completed_points,
                logged_hours=logged_hours,
                completed_logged_hours=completed_logged_hours,
                story_point_ratio=story_point_ratio,
                percent_complete=percent_complete,
                in_planning=in_planning,
                completed=completed,
                target_start=epic.target_start,
                target_end=epic.target_end,
                source_row=epic.source_row,
                jira_key=epic.key,
                row_role=assignment.row_role,
                fix_version=assignment.fix_version,
                drives_schedule=assignment.drives_schedule,
                primary_schedule_key=assignment.primary_schedule_key,
            )
        add_multi_fixversion_audit(audit, epic, assignments, rollup_mode)

    apply_dependencies(planned_epics, epics, audit)
    summaries = build_summaries(planned_epics, config)
    compare_with_baseline(planned_epics, summaries, baseline, config, audit)
    driving_epics = [epic for epic in planned_epics.values() if epic.drives_schedule]
    driving_logged_hours = round(sum(epic.logged_hours for epic in driving_epics), 2)
    driving_completed_logged_hours = round(sum(epic.completed_logged_hours for epic in driving_epics), 2)
    driving_completed_points = round(sum(epic.completed_story_points for epic in driving_epics), 2)

    stats = {
        "csv_rows_read": len(table.rows),
        "jira_issues_read": len(issues),
        "initiatives_read": len(initiatives),
        "epics_read": len(epics),
        "story_rows_used_for_completion": len(stories),
        "epics_included": len({epic.jira_key or epic.key for epic in planned_epics.values()}),
        "planned_epic_rows": len(planned_epics),
        "epics_excluded": excluded_count,
        "summary_rows": len(summaries),
        "audit_items": len(audit),
        "project_keys": sorted({epic.key_prefix for epic in planned_epics.values()}),
        "logged_hours": driving_logged_hours,
        "completed_logged_hours": driving_completed_logged_hours,
        "story_point_ratio": calculate_story_point_ratio(
            driving_completed_logged_hours,
            driving_completed_points,
            hours_per_story_point,
        ),
        "hours_per_story_point": hours_per_story_point,
        "rollup_modes_by_prefix": config.get("rollup_modes", {}),
        "multi_fixversion_epics": len(
            {
                epic.jira_key or epic.key
                for epic in planned_epics.values()
                if epic.row_role in {"Primary", "Reference", "Split"}
            }
        ),
    }
    return RunPlan(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        jira_csv=str(jira_csv),
        rollup_mode=describe_rollup_modes(planned_epics, config),
        column_map=column_map,
        stats=stats,
        summaries=summaries,
        epics=planned_epics,
        audit_items=audit,
    )


__all__ = [
    "AuditItem",
    "CsvTable",
    "J2PError",
    "JIRA_KEY_RE",
    "JiraIssue",
    "PlanEpic",
    "PlanSummary",
    "ProjectTaskSnapshot",
    "RollupAssignment",
    "RunPlan",
    "add_added_epic_audit",
    "add_dependency_review",
    "add_multi_fixversion_audit",
    "apply_dependencies",
    "audit_to_rows",
    "build_run_plan",
    "build_summaries",
    "calculate_percent",
    "calculate_story_point_ratio",
    "compare_field",
    "compare_with_baseline",
    "creates_cycle",
    "describe_rollup_modes",
    "fix_version_schedule_key",
    "format_number",
    "html_escape",
    "jira_key_prefix",
    "multi_fixversion_policy_for_prefix",
    "normalize_header",
    "parse_date",
    "parse_issue_keys",
    "parse_issues",
    "parse_logged_hours",
    "parse_number",
    "primary_planned_key",
    "resolve_rollup_assignments",
    "rollup_mode_for_prefix",
    "run_plan_to_state",
    "snapshots_from_state",
    "split_multi_values",
    "story_point_ratio_field_name",
    "summary_id",
    "write_json",
]
