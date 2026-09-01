"""Rollup assignment and summary-building logic."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Sequence, Tuple

from .metrics import calculate_percent, calculate_story_point_ratio
from .models import AuditItem, J2PError, JiraIssue, PlanEpic, PlanSummary, RollupAssignment


def resolve_rollup_assignments(
    epic: JiraIssue,
    rollup_mode: str,
    initiatives: Dict[str, JiraIssue],
    config: Dict[str, Any],
    prefix: str,
) -> Tuple[List[RollupAssignment], str]:
    if rollup_mode == "initiative":
        if not epic.parent:
            return [], "Epic has no initiative parent key."
        initiative = initiatives.get(epic.parent)
        if not initiative:
            return [], f"Epic parent '{epic.parent}' was not found as an Initiative row in the CSV."
        return [
            RollupAssignment(
                schedule_key=epic.key,
                rollup_key=initiative.key,
                rollup_name=initiative.summary or initiative.key,
                row_role="Scheduled",
                fix_version="",
                drives_schedule=True,
                primary_schedule_key=epic.key,
            )
        ], ""

    fix_versions = epic.fix_versions
    if not fix_versions:
        return [], "Epic has no fixVersion."
    if len(fix_versions) > 1:
        policy = multi_fixversion_policy_for_prefix(config, prefix)
        primary_schedule_key = epic.key
        assignments = []
        for index, fix_version in enumerate(fix_versions):
            schedule_key = epic.key if index == 0 else fix_version_schedule_key(epic.key, fix_version)
            assignments.append(
                RollupAssignment(
                    schedule_key=schedule_key,
                    rollup_key=fix_version,
                    rollup_name=fix_version,
                    row_role="Reference" if policy == "reference" and index > 0 else (
                        "Primary" if policy == "reference" else "Split"
                    ),
                    fix_version=fix_version,
                    drives_schedule=policy == "split" or index == 0,
                    primary_schedule_key=primary_schedule_key,
                )
            )
        return assignments, ""
    return [
        RollupAssignment(
            schedule_key=epic.key,
            rollup_key=fix_versions[0],
            rollup_name=fix_versions[0],
            row_role="Scheduled",
            fix_version=fix_versions[0],
            drives_schedule=True,
            primary_schedule_key=epic.key,
        )
    ], ""


def add_multi_fixversion_audit(
    audit: List[AuditItem],
    epic: JiraIssue,
    assignments: Sequence[RollupAssignment],
    rollup_mode: str,
) -> None:
    if rollup_mode != "fixVersion" or len(assignments) <= 1:
        return
    policy = "reference" if any(not item.drives_schedule for item in assignments) else "split"
    primary = assignments[0]
    for assignment in assignments:
        if policy == "reference" and assignment.row_role == "Reference":
            message = (
                f"Reference row created under fixVersion '{assignment.fix_version}'. "
                f"Schedule is driven by primary row '{primary.schedule_key}' under "
                f"fixVersion '{primary.fix_version}'."
            )
            reviewer_action = "Confirm this secondary fixVersion is for visibility only."
        elif policy == "reference":
            message = (
                f"Primary scheduled row selected from the first Jira fixVersion '{assignment.fix_version}'. "
                "Secondary fixVersions are added as non-driving reference rows."
            )
            reviewer_action = "Confirm the first Jira fixVersion should drive the schedule."
        else:
            message = (
                f"Split scheduled row created under fixVersion '{assignment.fix_version}'. "
                "Each split row can drive the Project schedule."
            )
            reviewer_action = "Confirm this epic should drive schedule dates under each listed fixVersion."
        audit.append(
            AuditItem(
                "Info",
                "MultiFixVersionReference" if policy == "reference" else "MultiFixVersionSplit",
                jira_key=epic.key,
                schedule_key=assignment.schedule_key,
                issue_type=epic.issue_type,
                summary=epic.summary,
                field="Fix versions",
                new_value=", ".join(epic.fix_versions),
                message=message,
                reviewer_action=reviewer_action,
                source_row=epic.source_row,
            )
        )


def build_summaries(epics: Dict[str, PlanEpic], config: Dict[str, Any]) -> Dict[str, PlanSummary]:
    hours_per_story_point = float(config.get("metrics", {}).get("hours_per_story_point", 8.0))
    buckets: Dict[str, List[PlanEpic]] = {}
    for epic in epics.values():
        buckets.setdefault(summary_id(epic.rollup_mode, epic.rollup_key), []).append(epic)
    summaries: Dict[str, PlanSummary] = {}
    for bucket_id, children in sorted(buckets.items()):
        driving_children = [child for child in children if child.drives_schedule]
        reference_children = [child for child in children if not child.drives_schedule]
        total = round(sum(child.total_story_points for child in driving_children), 2)
        completed = round(sum(child.completed_story_points for child in driving_children), 2)
        logged_hours = round(sum(child.logged_hours for child in driving_children), 2)
        completed_logged_hours = round(sum(child.completed_logged_hours for child in driving_children), 2)
        ratio_completed = completed
        if driving_children:
            percent_complete = calculate_percent(completed, total)
        else:
            reference_total = round(sum(child.total_story_points for child in reference_children), 2)
            reference_completed = round(sum(child.completed_story_points for child in reference_children), 2)
            logged_hours = round(sum(child.logged_hours for child in reference_children), 2)
            completed_logged_hours = round(sum(child.completed_logged_hours for child in reference_children), 2)
            percent_complete = calculate_percent(reference_completed, reference_total)
            ratio_completed = reference_completed
        story_point_ratio = calculate_story_point_ratio(
            completed_logged_hours,
            ratio_completed,
            hours_per_story_point,
        )
        project_keys = sorted({child.key_prefix for child in children})
        summaries[bucket_id] = PlanSummary(
            summary_id=bucket_id,
            key=children[0].rollup_key,
            name=children[0].rollup_name,
            rollup_mode=children[0].rollup_mode,
            project_key=project_keys[0] if len(project_keys) == 1 else "MULTIPLE",
            total_story_points=total,
            completed_story_points=completed,
            logged_hours=logged_hours,
            completed_logged_hours=completed_logged_hours,
            story_point_ratio=story_point_ratio,
            percent_complete=percent_complete,
            child_epic_count=len(children),
            driving_epic_count=len(driving_children),
            reference_epic_count=len(reference_children),
        )
    return summaries


def rollup_mode_for_prefix(config: Dict[str, Any], prefix: str) -> str:
    prefix_text = prefix.upper()
    rollup_mode = config.get("rollup_modes", {}).get(prefix_text)
    if not rollup_mode:
        raise J2PError(
            f"rollup_modes.{prefix_text} is required because {prefix_text} is configured in resource_groups."
        )
    return str(rollup_mode)


def multi_fixversion_policy_for_prefix(config: Dict[str, Any], prefix: str) -> str:
    policy_config = config.get("multi_fixversion_policy", {})
    return str(policy_config.get(prefix.upper(), policy_config.get("default", "reference")))


def fix_version_schedule_key(jira_key: str, fix_version: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", fix_version).strip("-").upper() or "FIXVERSION"
    digest = hashlib.sha1(fix_version.encode("utf-8")).hexdigest()[:8]
    return f"{jira_key}::FV::{cleaned[:40]}::{digest}".upper()


def summary_id(rollup_mode: str, rollup_key: str) -> str:
    return f"{rollup_mode}:{rollup_key}"


def describe_rollup_modes(epics: Dict[str, PlanEpic], config: Dict[str, Any]) -> str:
    modes = sorted({epic.rollup_mode for epic in epics.values()})
    if len(modes) == 1:
        return modes[0]
    if len(modes) > 1:
        return "mixed"
    configured_modes = sorted(set(config.get("rollup_modes", {}).values()))
    if len(configured_modes) == 1:
        return configured_modes[0]
    if len(configured_modes) > 1:
        return "mixed"
    return ""
