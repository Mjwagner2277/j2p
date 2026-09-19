"""Core Jira CSV planning orchestration.

The focused implementation lives in smaller modules. This module remains the
public planning facade used by the CLI and tests.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .baseline import (
    add_added_epic_audit,
    compare_field,
    compare_with_baseline,
    story_point_ratio_field_name,
)
from .project_values import validate_project_plan
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
    jira_csv: Union[Path, Sequence[Path]],
    config: Dict[str, Any],
    baseline: Optional[Dict[str, ProjectTaskSnapshot]] = None,
) -> RunPlan:
    paths = [jira_csv] if isinstance(jira_csv, (str, Path)) else list(jira_csv)
    if not paths:
        raise J2PError("At least one Jira CSV is required.")
    audit: List[AuditItem] = []
    baseline = baseline or {}
    required = ["jira_key", "issue_type", "summary", "epic_link", "story_points", "status"]
    configured_rollup_modes = set(config.get("rollup_modes", {}).values())
    if "initiative" in configured_rollup_modes:
        required.append("parent")
    if "fixVersion" in configured_rollup_modes:
        required.append("fix_versions")
    column_headers = {name: [] for name in sorted(config.get("columns", {}))}
    issues = []
    seen = {}
    duplicate_count = 0
    csv_batches = []
    for path in paths:
        table = CsvTable(Path(path), config.get("input", {}).get("csv_max_field_chars", 8 * 1024 * 1024))
        missing = [name for name in required if not table.has_any(logical_columns(config, name))]
        if missing:
            details = ", ".join(f"{name}: {logical_columns(config, name)}" for name in missing)
            raise J2PError(f"CSV {table.path} is missing required mapped columns: {details}")

        batch_column_map = {}
        for name in column_headers:
            header = table.selected_header(logical_columns(config, name))
            batch_column_map[name] = header
            if header and header not in column_headers[name]:
                column_headers[name].append(header)
        batch_metadata = {
            "path": str(table.path.resolve()), "encoding": table.encoding,
            "rows_read": len(table.rows), "issues_read": 0,
            "unique_issues_added": 0, "duplicates_skipped": 0,
            "column_map": batch_column_map,
        }
        batch_audit = []
        batch = parse_issues(table, config, batch_audit)
        batch_metadata["issues_read"] = len(batch)
        for item in batch_audit:
            item.source_file = str(table.path)
        audit.extend(batch_audit)
        for issue in batch:
            prior = seen.get(issue.key)
            if prior is not None:
                current_values = asdict(issue)
                prior_values = asdict(prior)
                for metadata in ("source_row", "source_file"):
                    current_values.pop(metadata)
                    prior_values.pop(metadata)
                if current_values != prior_values:
                    changed_fields = sorted(
                        name for name, value in current_values.items() if value != prior_values[name]
                    )
                    raise J2PError(
                        f"Conflicting duplicate Jira key {issue.key}: "
                        f"{prior.source_file} row {prior.source_row} and "
                        f"{issue.source_file} row {issue.source_row}. "
                        f"Conflicting fields: {', '.join(changed_fields)}. "
                        "Export consistent batches from the same snapshot; no version was selected."
                    )
                duplicate_count += 1
                batch_metadata["duplicates_skipped"] += 1
                audit.append(AuditItem(
                    "Info", "DuplicateCsvIssueSkipped", jira_key=issue.key,
                    message=f"Repeated issue counted once; first seen in {prior.source_file} row {prior.source_row}.",
                    source_row=issue.source_row, source_file=issue.source_file,
                ))
                continue
            seen[issue.key] = issue
            issues.append(issue)
            batch_metadata["unique_issues_added"] += 1
        csv_batches.append(batch_metadata)
        # Retain normalized issues and compact provenance, not every raw export.
        del batch, table
    column_map = {name: " | ".join(headers) for name, headers in column_headers.items()}
    issues_by_key = {issue.key.upper(): issue for issue in issues if issue.key}
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
    if config.get("metrics", {}).get("logged_hours_source", "direct") == "aggregate":
        validate_aggregate_time_scope(stories)

    story_rollup_by_epic: Dict[str, Dict[str, float]] = {}
    stories_by_epic: Dict[str, List[JiraIssue]] = {}
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
        parent_issue = issues_by_key.get(story.epic_link)
        if parent_issue is None or parent_issue.issue_type.strip().lower() not in issue_type_sets["epic"]:
            missing_parent = parent_issue is None
            audit.append(AuditItem(
                "Warning", "StoryEpicNotFound" if missing_parent else "StoryParentNotEpic",
                jira_key=story.key, issue_type=story.issue_type, summary=story.summary,
                field="Epic Link", old_value=story.epic_link,
                message=(
                    f"Child work links to {story.epic_link}, which is "
                    + ("missing from the combined export." if missing_parent else f"a {parent_issue.issue_type}, not an epic.")
                    + " Its points and logged hours are omitted from the schedule."
                ),
                reviewer_action="Include the parent epic in an export batch or correct the Epic Link.",
                source_row=story.source_row, source_file=story.source_file,
            ))
            continue
        stories_by_epic.setdefault(story.epic_link.upper(), []).append(story)
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
    fixversion_planning_dates = earliest_dates_by_fixversion(issues)

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
            planning_date = epic_planning_date(
                epic,
                stories_by_epic.get(epic.key.upper(), []),
                rollup_mode,
                assignments,
                fixversion_planning_dates,
            )
            planning_bucket = planning_bucket_for_date(planning_date, config)
            future_planning = planning_bucket not in {"", "Immediate", "Unscheduled"}
            audit.append(
                AuditItem(
                    "Info" if future_planning else "Review",
                    "FutureInPlanning" if future_planning else "InPlanning",
                    jira_key=epic.key,
                    issue_type=epic.issue_type,
                    summary=epic.summary,
                    field="In Planning",
                    new_value="Yes",
                    color="in_planning",
                    message=(
                        "Epic has no pointed child stories/tasks and is marked In Planning. "
                        f"Planning horizon: {planning_bucket}."
                    ),
                    reviewer_action=(
                        "No immediate task-breakdown action is expected until this item enters the "
                        "Immediate planning window."
                        if future_planning
                        else "Confirm this epic is intentionally unestimated or add pointed child work."
                    ),
                    source_row=epic.source_row,
                    planning_date=planning_date,
                    planning_bucket=planning_bucket,
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

    included_jira_keys = {epic.jira_key or epic.key for epic in planned_epics.values()}
    attached_story_count = sum(len(children) for children in stories_by_epic.values())
    used_story_count = 0
    for parent_key, children in stories_by_epic.items():
        if parent_key in included_jira_keys:
            used_story_count += len(children)
            continue
        for story in children:
            audit.append(AuditItem(
                "Warning", "StoryEpicExcluded", jira_key=story.key,
                issue_type=story.issue_type, summary=story.summary, field="Epic Link",
                old_value=parent_key,
                message=f"Parent epic {parent_key} was excluded by resource or rollup rules. This child's points and logged hours are omitted from the schedule.",
                reviewer_action="Review the parent epic's exclusion or confirm this child work is outside the schedule scope.",
                source_row=story.source_row, source_file=story.source_file,
            ))

    apply_dependencies(planned_epics, epics, audit)
    summaries = build_summaries(planned_epics, config)
    compare_with_baseline(planned_epics, summaries, baseline, config, audit)
    fixversion_suppression_stats = apply_completed_fixversion_suppression(
        issues,
        planned_epics,
        summaries,
        config,
        audit,
        has_resolved_column=bool(column_map.get("resolved")),
    )
    audit, suppressed_audit_count = suppress_historical_audit_items(
        audit,
        issues_by_key,
        planned_epics,
        config,
    )
    assign_audit_planning_buckets(audit, issues, issues_by_key, planned_epics, config)
    driving_epics = [epic for epic in planned_epics.values() if epic.drives_schedule]
    driving_logged_hours = round(sum(epic.logged_hours for epic in driving_epics), 2)
    driving_completed_logged_hours = round(sum(epic.completed_logged_hours for epic in driving_epics), 2)
    driving_completed_points = round(sum(epic.completed_story_points for epic in driving_epics), 2)

    stats = {
        "csv_rows_read": sum(batch["rows_read"] for batch in csv_batches),
        "csv_files_read": len(csv_batches),
        "csv_batches": csv_batches,
        "duplicate_csv_issues_skipped": duplicate_count,
        "jira_issues_read": len(issues),
        "unique_issues_read": len(issues),
        "initiatives_read": len(initiatives),
        "epics_read": len(epics),
        "story_rows_read": len(stories),
        "story_rows_attached_to_epic": attached_story_count,
        "story_rows_used_for_completion": used_story_count,
        "story_rows_omitted_from_completion": len(stories) - used_story_count,
        "epics_included": len({epic.jira_key or epic.key for epic in planned_epics.values()}),
        "planned_epic_rows": len(planned_epics),
        "epics_excluded": excluded_count,
        "summary_rows": len(summaries),
        "audit_items": len(audit),
        "suppressed_audit_items": suppressed_audit_count,
        **fixversion_suppression_stats,
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
    plan = RunPlan(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        jira_csv="; ".join(str(path) for path in paths),
        rollup_mode=describe_rollup_modes(planned_epics, config),
        column_map=column_map,
        stats=stats,
        summaries=summaries,
        epics=planned_epics,
        audit_items=audit,
    )

    for epic in plan.epics.values():
        source = issues_by_key.get(epic.jira_key or epic.key)
        if source:
            epic.source_file = source.source_file
    for item in plan.audit_items:
        source = issues_by_key.get(item.jira_key)
        if source and not item.source_file:
            item.source_file = source.source_file
    validate_project_plan(plan, config)
    return plan


def validate_aggregate_time_scope(stories: List[JiraIssue]) -> None:
    """Aggregate exports must contain one nonoverlapping level of child work."""
    by_key = {story.key: story for story in stories}
    for story in stories:
        if story.parent in by_key:
            raise J2PError(
                f"CSV {story.source_file}, row {story.source_row}, Jira key {story.key}: "
                f"aggregate logged time overlaps exported parent {story.parent}. "
                "Export direct logged time, or export only one child hierarchy level."
            )
        if story.issue_type.strip().lower() in {"sub-task", "subtask"} and not story.parent:
            raise J2PError(
                f"CSV {story.source_file}, row {story.source_row}, Jira key {story.key}: "
                "aggregate logged time for a subtask requires its Parent key to verify nonoverlapping totals."
            )


def apply_completed_fixversion_suppression(
    issues: List[JiraIssue],
    planned_epics: Dict[str, PlanEpic],
    summaries: Dict[str, PlanSummary],
    config: Dict[str, Any],
    audit: List[AuditItem],
    has_resolved_column: bool,
) -> Dict[str, Any]:
    settings = config.get("fixversion_completion_suppression", {})
    stale_after_days = int(settings.get("stale_after_days", 90))
    stats: Dict[str, Any] = {
        "suppressed_completed_fixversion_summary_ids": [],
        "suppressed_completed_fixversion_rollups": 0,
        "suppressed_completed_fixversion_epic_rows": 0,
        "fixversion_completion_suppression_active": False,
        "fixversion_completion_suppression_as_of": "",
        "fixversion_completion_suppression_stale_after_days": stale_after_days,
    }
    if not settings.get("enabled", True) or not has_resolved_column:
        return stats

    as_of = fixversion_suppression_as_of(settings)
    cutoff = as_of - timedelta(days=stale_after_days)
    done_statuses = lowered(config.get("done_statuses", []))
    issues_by_fixversion: Dict[str, List[JiraIssue]] = {}
    for issue in issues:
        for fix_version in issue.fix_versions:
            issues_by_fixversion.setdefault(fix_version, []).append(issue)

    suppressed_ids: List[str] = []
    for summary in sorted(summaries.values(), key=lambda item: (item.rollup_mode, item.key)):
        if summary.rollup_mode != "fixVersion":
            continue
        # Choose audit provenance by issue identity, independent of CSV order.
        fix_version_issues = sorted(
            issues_by_fixversion.get(summary.key, []), key=lambda issue: issue.key
        )
        if not fix_version_issues:
            continue
        if not all(issue.status.strip().lower() in done_statuses for issue in fix_version_issues):
            continue
        resolved_dates = [
            normalized_iso_date(issue.resolved)
            for issue in fix_version_issues
            if normalized_iso_date(issue.resolved)
        ]
        if len(resolved_dates) != len(fix_version_issues):
            missing = [issue for issue in fix_version_issues if not normalized_iso_date(issue.resolved)]
            audit.append(
                AuditItem(
                    "Review",
                    "CompletedFixVersionMissingResolvedDate",
                    jira_key=missing[0].key if missing else "",
                    schedule_key=summary.summary_id,
                    issue_type="fixVersion",
                    summary=summary.name,
                    field="Resolved",
                    color="review_needed",
                    message=(
                        f"FixVersion '{summary.key}' is complete by status but "
                        f"{len(missing)} issue(s) are missing a usable Resolved date."
                    ),
                    reviewer_action=(
                        "Keep the fixVersion visible until Jira Resolved dates are populated "
                        "or suppression is intentionally disabled."
                    ),
                    source_row=missing[0].source_row if missing else None,
                )
            )
            continue

        latest_resolved = max(resolved_dates)
        latest_resolved_date = date.fromisoformat(latest_resolved)
        if latest_resolved_date >= cutoff:
            continue

        suppressed_ids.append(summary.summary_id)
        if settings.get("keep_audit_summary", True):
            audit.append(
                AuditItem(
                    "Info",
                    "SuppressedCompletedFixVersion",
                    jira_key=fix_version_issues[0].key,
                    schedule_key=summary.summary_id,
                    issue_type="fixVersion",
                    summary=summary.name,
                    field="Resolved",
                    old_value=latest_resolved,
                    new_value=f"Hidden from manager HTML reports after {stale_after_days} days",
                    message=(
                        f"FixVersion '{summary.key}' is complete by Jira status and its latest "
                        f"Resolved date is older than {stale_after_days} days."
                    ),
                    reviewer_action="No manager review needed unless the release should remain visible.",
                    source_row=fix_version_issues[0].source_row,
                )
            )

    suppressed_id_set = set(suppressed_ids)
    hidden_epic_rows = [
        epic
        for epic in planned_epics.values()
        if summary_id(epic.rollup_mode, epic.rollup_key) in suppressed_id_set
    ]
    stats.update(
        {
            "suppressed_completed_fixversion_summary_ids": sorted(suppressed_id_set),
            "suppressed_completed_fixversion_rollups": len(suppressed_id_set),
            "suppressed_completed_fixversion_epic_rows": len(hidden_epic_rows),
            "fixversion_completion_suppression_active": True,
            "fixversion_completion_suppression_as_of": as_of.isoformat(),
        }
    )
    return stats


def fixversion_suppression_as_of(settings: Dict[str, Any]) -> date:
    raw_as_of = str(settings.get("as_of_date", "") or "").strip()
    if not raw_as_of:
        return datetime.now().date()
    audit: List[AuditItem] = []
    parsed = parse_date(raw_as_of, audit, "CONFIG", 0)
    if audit or not normalized_iso_date(parsed):
        raise J2PError(
            "fixversion_completion_suppression.as_of_date must be a valid date. "
            "Use YYYY-MM-DD, for example 2026-09-17."
        )
    return date.fromisoformat(parsed)


def assign_audit_planning_buckets(
    audit: List[AuditItem],
    issues: List[JiraIssue],
    issues_by_key: Dict[str, JiraIssue],
    planned_epics: Dict[str, PlanEpic],
    config: Dict[str, Any],
) -> None:
    if not config.get("planning_horizon", {}).get("enabled", True):
        return

    stories_by_epic: Dict[str, List[JiraIssue]] = {}
    for issue in issues:
        if issue.epic_link:
            stories_by_epic.setdefault(issue.epic_link.upper(), []).append(issue)
    fixversion_dates = earliest_dates_by_fixversion(issues)
    for item in audit:
        planning_date = item.planning_date or audit_item_planning_date(
            item,
            issues_by_key,
            planned_epics,
            stories_by_epic,
            fixversion_dates,
        )
        item.planning_date = planning_date
        item.planning_bucket = item.planning_bucket or planning_bucket_for_date(planning_date, config)


def audit_item_planning_date(
    item: AuditItem,
    issues_by_key: Dict[str, JiraIssue],
    planned_epics: Dict[str, PlanEpic],
    stories_by_epic: Dict[str, List[JiraIssue]],
    fixversion_dates: Dict[str, str],
) -> str:
    schedule_key = (item.schedule_key or "").strip()
    planned = planned_epics.get(schedule_key)
    if planned:
        return planned_epic_planning_date(planned, issues_by_key, stories_by_epic, fixversion_dates)

    if schedule_key.startswith("fixVersion:"):
        fix_version = schedule_key.split(":", 1)[1]
        if fixversion_dates.get(fix_version):
            return fixversion_dates[fix_version]

    jira_key = (item.jira_key or schedule_key.split("::", 1)[0]).upper()
    if jira_key:
        issue = issues_by_key.get(jira_key)
        if issue:
            issue_date = issue_planning_date(issue)
            child_dates = [issue_planning_date(child) for child in stories_by_epic.get(jira_key, [])]
            return earliest_date([issue_date, *child_dates])
        for epic in planned_epics.values():
            if (epic.jira_key or epic.key).upper() == jira_key:
                return planned_epic_planning_date(epic, issues_by_key, stories_by_epic, fixversion_dates)

    return ""


def planned_epic_planning_date(
    epic: PlanEpic,
    issues_by_key: Dict[str, JiraIssue],
    stories_by_epic: Dict[str, List[JiraIssue]],
    fixversion_dates: Dict[str, str],
) -> str:
    if epic.rollup_mode == "fixVersion":
        fix_version = epic.fix_version or epic.rollup_key
        if fixversion_dates.get(fix_version):
            return fixversion_dates[fix_version]

    jira_key = (epic.jira_key or epic.key).upper()
    issue = issues_by_key.get(jira_key)
    dates = [epic.target_start, epic.target_end]
    if issue:
        dates.append(issue_planning_date(issue))
    dates.extend(issue_planning_date(child) for child in stories_by_epic.get(jira_key, []))
    return earliest_date(dates)


def epic_planning_date(
    epic: JiraIssue,
    children: List[JiraIssue],
    rollup_mode: str,
    assignments: List[RollupAssignment],
    fixversion_dates: Dict[str, str],
) -> str:
    if rollup_mode == "fixVersion":
        assignment_dates = [
            fixversion_dates.get(assignment.fix_version or assignment.rollup_key, "")
            for assignment in assignments
        ]
        assignment_date = earliest_date(assignment_dates)
        if assignment_date:
            return assignment_date

    return earliest_date([issue_planning_date(epic), *[issue_planning_date(child) for child in children]])


def earliest_dates_by_fixversion(issues: List[JiraIssue]) -> Dict[str, str]:
    dates_by_fixversion: Dict[str, List[str]] = {}
    for issue in issues:
        issue_date = issue_planning_date(issue)
        if not issue_date:
            continue
        for fix_version in issue.fix_versions:
            dates_by_fixversion.setdefault(fix_version, []).append(issue_date)
    return {
        fix_version: earliest_date(dates)
        for fix_version, dates in dates_by_fixversion.items()
    }


def issue_planning_date(issue: JiraIssue) -> str:
    return earliest_date([issue.target_start, issue.target_end])


def earliest_date(values: List[Any]) -> str:
    dates = [normalized_iso_date(value) for value in values if normalized_iso_date(value)]
    return min(dates) if dates else ""


def planning_bucket_for_date(value: str, config: Dict[str, Any]) -> str:
    settings = config.get("planning_horizon", {})
    if not settings.get("enabled", True):
        return ""
    date_text = normalized_iso_date(value)
    if not date_text:
        return "Unscheduled"

    item_date = date.fromisoformat(date_text)
    as_of = planning_horizon_as_of(settings)
    immediate_months = int(settings.get("immediate_months", 6))
    bucket_months = int(settings.get("bucket_months", 6))
    if item_date < add_months(as_of, immediate_months):
        return "Immediate"

    bucket_start = immediate_months
    while item_date >= add_months(as_of, bucket_start + bucket_months):
        bucket_start += bucket_months
    return f"{bucket_start}-{bucket_start + bucket_months} Months"


def planning_horizon_as_of(settings: Dict[str, Any]) -> date:
    raw_as_of = str(settings.get("as_of_date", "") or "").strip()
    if not raw_as_of:
        return datetime.now().date()
    audit: List[AuditItem] = []
    parsed = parse_date(raw_as_of, audit, "CONFIG", 0)
    if audit or not normalized_iso_date(parsed):
        raise J2PError(
            "planning_horizon.as_of_date must be a valid date. "
            "Use YYYY-MM-DD, for example 2026-09-17."
        )
    return date.fromisoformat(parsed)


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def suppress_historical_audit_items(
    audit: List[AuditItem],
    issues_by_key: Dict[str, JiraIssue],
    planned_epics: Dict[str, PlanEpic],
    config: Dict[str, Any],
) -> tuple[List[AuditItem], int]:
    settings = config.get("warning_suppression", {})
    cutoff = warning_suppression_cutoff(settings)
    if not cutoff:
        return audit, 0

    severities = set(settings.get("severities", ["Warning", "Review"]))
    date_fields = settings.get("date_fields", ["target_end", "target_start"])
    kept: List[AuditItem] = []
    suppressed_count = 0
    for item in audit:
        if item.severity not in severities:
            kept.append(item)
            continue
        item_date = audit_item_suppression_date(item, issues_by_key, planned_epics, date_fields)
        if item_date and item_date < cutoff:
            suppressed_count += 1
            continue
        kept.append(item)

    if suppressed_count and settings.get("keep_summary", True):
        kept.append(
            AuditItem(
                "Info",
                "SuppressedHistoricalWarnings",
                message=(
                    f"Suppressed {suppressed_count} audit item(s) before {cutoff} "
                    "using warning_suppression.before."
                ),
                reviewer_action=(
                    "Historical Jira items were intentionally hidden from this run's review noise. "
                    "Adjust warning_suppression.before if older issues need to be reviewed."
                ),
            )
        )
    return kept, suppressed_count


def warning_suppression_cutoff(settings: Dict[str, Any]) -> str:
    raw_cutoff = str(settings.get("before", "") or "").strip()
    if not raw_cutoff:
        return ""
    audit: List[AuditItem] = []
    parsed = parse_date(raw_cutoff, audit, "CONFIG", 0)
    if audit or not normalized_iso_date(parsed):
        raise J2PError(
            "warning_suppression.before must be a valid date. "
            "Use YYYY-MM-DD, for example 2025-01-01."
        )
    return parsed


def audit_item_suppression_date(
    item: AuditItem,
    issues_by_key: Dict[str, JiraIssue],
    planned_epics: Dict[str, PlanEpic],
    date_fields: List[str],
) -> str:
    candidates: List[Any] = []
    schedule_key = (item.schedule_key or "").upper()
    jira_key = (item.jira_key or schedule_key.split("::", 1)[0]).upper()

    planned = planned_epics.get(schedule_key)
    if planned:
        candidates.append(planned)
    if jira_key:
        issue = issues_by_key.get(jira_key)
        if issue:
            candidates.append(issue)
        for epic in planned_epics.values():
            if (epic.jira_key or epic.key).upper() == jira_key and epic not in candidates:
                candidates.append(epic)

    for field_name in date_fields:
        for candidate in candidates:
            date_value = normalized_iso_date(getattr(candidate, field_name, ""))
            if date_value:
                return date_value
    return ""


def normalized_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


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
    "apply_completed_fixversion_suppression",
    "suppress_historical_audit_items",
    "write_json",
]
