"""Core Jira CSV planning logic."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import logical_columns, lowered


JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


class J2PError(RuntimeError):
    """Raised when j2p cannot continue."""


@dataclass
class AuditItem:
    severity: str
    category: str
    jira_key: str = ""
    schedule_key: str = ""
    issue_type: str = ""
    summary: str = ""
    field: str = ""
    old_value: str = ""
    new_value: str = ""
    color: str = ""
    message: str = ""
    reviewer_action: str = ""
    source_row: Optional[int] = None


@dataclass
class JiraIssue:
    key: str
    issue_id: str
    issue_type: str
    summary: str
    epic_link: str
    parent: str
    fix_versions: List[str]
    story_points: Optional[float]
    status: str
    resolution: str
    target_start: str
    target_end: str
    predecessors: Set[str]
    successors: Set[str]
    source_row: int


@dataclass
class PlanEpic:
    key: str
    issue_id: str
    summary: str
    status: str
    rollup_mode: str
    rollup_key: str
    rollup_name: str
    resource_group: str
    key_prefix: str
    total_story_points: float
    completed_story_points: float
    percent_complete: int
    in_planning: bool
    completed: bool
    target_start: str
    target_end: str
    predecessors: List[str] = field(default_factory=list)
    successors: List[str] = field(default_factory=list)
    dependency_review: str = ""
    source_row: Optional[int] = None
    jira_key: str = ""
    row_role: str = "Scheduled"
    fix_version: str = ""
    drives_schedule: bool = True
    primary_schedule_key: str = ""


@dataclass
class PlanSummary:
    summary_id: str
    key: str
    name: str
    rollup_mode: str
    project_key: str
    total_story_points: float
    completed_story_points: float
    percent_complete: int
    child_epic_count: int
    driving_epic_count: int = 0
    reference_epic_count: int = 0


@dataclass
class ProjectTaskSnapshot:
    key: str
    jira_key: str = ""
    name: str = ""
    issue_id: str = ""
    issue_type: str = ""
    rollup_mode: str = ""
    rollup_key: str = ""
    resource_group: str = ""
    key_prefix: str = ""
    total_story_points: float = 0.0
    completed_story_points: float = 0.0
    percent_complete: int = 0
    status: str = ""
    target_start: str = ""
    target_end: str = ""
    start: str = ""
    finish: str = ""
    predecessors: List[str] = field(default_factory=list)
    successors: List[str] = field(default_factory=list)
    row_role: str = ""
    fix_version: str = ""
    drives_schedule: Optional[bool] = None
    primary_schedule_key: str = ""
    is_summary: bool = False
    active: Optional[bool] = None
    source: str = ""


@dataclass
class RunPlan:
    generated_at: str
    jira_csv: str
    rollup_mode: str
    column_map: Dict[str, str]
    stats: Dict[str, Any]
    summaries: Dict[str, PlanSummary]
    epics: Dict[str, PlanEpic]
    audit_items: List[AuditItem]


@dataclass(frozen=True)
class RollupAssignment:
    schedule_key: str
    rollup_key: str
    rollup_name: str
    row_role: str
    fix_version: str
    drives_schedule: bool
    primary_schedule_key: str


class CsvTable:
    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            raise J2PError(f"CSV is empty: {path}")
        self.headers = [header.strip() for header in rows[0]]
        self.rows = [row for row in rows[1:] if any(cell.strip() for cell in row)]
        self.header_index: Dict[str, List[int]] = {}
        for index, header in enumerate(self.headers):
            self.header_index.setdefault(normalize_header(header), []).append(index)

    def has_any(self, candidates: Sequence[str]) -> bool:
        return any(normalize_header(candidate) in self.header_index for candidate in candidates)

    def selected_header(self, candidates: Sequence[str]) -> str:
        for candidate in candidates:
            norm = normalize_header(candidate)
            if norm in self.header_index:
                return self.headers[self.header_index[norm][0]]
        return ""

    def get_all(self, row: Sequence[str], candidates: Sequence[str]) -> List[str]:
        values: List[str] = []
        for candidate in candidates:
            norm = normalize_header(candidate)
            for index in self.header_index.get(norm, []):
                if index < len(row):
                    value = row[index].strip()
                    if value:
                        values.append(value)
        return values

    def get_first(self, row: Sequence[str], candidates: Sequence[str]) -> str:
        values = self.get_all(row, candidates)
        return values[0] if values else ""


def build_run_plan(
    jira_csv: Path,
    config: Dict[str, Any],
    baseline: Optional[Dict[str, ProjectTaskSnapshot]] = None,
) -> RunPlan:
    table = CsvTable(jira_csv)
    audit: List[AuditItem] = []
    baseline = baseline or {}
    required = ["jira_key", "issue_type", "summary", "epic_link", "story_points", "status"]
    configured_rollup_modes = {str(config["rollup_mode"])} | set(config.get("rollup_modes", {}).values())
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

    story_points_by_epic: Dict[str, Dict[str, float]] = {}
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
        bucket = story_points_by_epic.setdefault(story.epic_link, {"total": 0.0, "completed": 0.0})
        bucket["total"] += points
        if story.status.strip().lower() in done_statuses:
            bucket["completed"] += points

    planned_epics: Dict[str, PlanEpic] = {}
    excluded_count = 0
    resource_groups = config.get("resource_groups", {})
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

        point_bucket = story_points_by_epic.get(epic.key, {"total": 0.0, "completed": 0.0})
        total_points = round(point_bucket["total"], 2)
        completed_points = round(point_bucket["completed"], 2)
        in_planning = total_points <= 0
        percent_complete = calculate_percent(completed_points, total_points)
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
    summaries = build_summaries(planned_epics)
    compare_with_baseline(planned_epics, summaries, baseline, config, audit)

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


def parse_issues(table: CsvTable, config: Dict[str, Any], audit: List[AuditItem]) -> List[JiraIssue]:
    issues: List[JiraIssue] = []
    columns = config["columns"]
    for row_index, row in enumerate(table.rows, start=2):
        key = table.get_first(row, columns["jira_key"]).upper()
        if not key:
            audit.append(
                AuditItem(
                    "Warning",
                    "CsvRowMissingJiraKey",
                    message=f"CSV row {row_index} has no Jira key and was skipped.",
                    reviewer_action="Correct the Jira export or remove the blank row.",
                    source_row=row_index,
                )
            )
            continue
        if not JIRA_KEY_RE.fullmatch(key):
            audit.append(
                AuditItem(
                    "Warning",
                    "UnexpectedJiraKeyFormat",
                    jira_key=key,
                    message="Jira key does not match the expected PREFIX-123 format.",
                    reviewer_action="Confirm the row is a valid Jira issue.",
                    source_row=row_index,
                )
            )
        issues.append(
            JiraIssue(
                key=key,
                issue_id=table.get_first(row, columns.get("issue_id", [])),
                issue_type=table.get_first(row, columns["issue_type"]),
                summary=table.get_first(row, columns["summary"]),
                epic_link=table.get_first(row, columns["epic_link"]).upper(),
                parent=table.get_first(row, columns.get("parent", [])).upper(),
                fix_versions=split_multi_values(table.get_all(row, columns.get("fix_versions", []))),
                story_points=parse_number(table.get_first(row, columns["story_points"])),
                status=table.get_first(row, columns["status"]),
                resolution=table.get_first(row, columns.get("resolution", [])),
                target_start=parse_date(table.get_first(row, columns.get("target_start", [])), audit, key, row_index),
                target_end=parse_date(table.get_first(row, columns.get("target_end", [])), audit, key, row_index),
                predecessors=parse_issue_keys(table.get_all(row, columns.get("predecessors", []))),
                successors=parse_issue_keys(table.get_all(row, columns.get("successors", []))),
                source_row=row_index,
            )
        )
    return issues


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


def apply_dependencies(
    planned_epics: Dict[str, PlanEpic],
    all_epics: List[JiraIssue],
    audit: List[AuditItem],
) -> None:
    row_keys_by_jira: Dict[str, List[str]] = {}
    driving_keys_by_jira: Dict[str, List[str]] = {}
    for schedule_key, planned_epic in planned_epics.items():
        jira_key = planned_epic.jira_key or planned_epic.key
        row_keys_by_jira.setdefault(jira_key, []).append(schedule_key)
        if planned_epic.drives_schedule:
            driving_keys_by_jira.setdefault(jira_key, []).append(schedule_key)

    included_keys = set(driving_keys_by_jira)
    raw_epics = {epic.key: epic for epic in all_epics}
    candidate_edges: List[Tuple[str, str, str]] = []
    for epic_key, epic in raw_epics.items():
        if epic_key not in row_keys_by_jira:
            continue
        for predecessor_key in sorted(epic.predecessors):
            candidate_edges.append((predecessor_key, epic_key, "blocked by"))
        for successor_key in sorted(epic.successors):
            candidate_edges.append((epic_key, successor_key, "blocks"))

    accepted: Set[Tuple[str, str]] = set()
    driving_schedule_keys = {key for keys in driving_keys_by_jira.values() for key in keys}
    graph: Dict[str, Set[str]] = {key: set() for key in driving_schedule_keys}
    seen: Set[Tuple[str, str]] = set()
    for predecessor_key, successor_key, relation in sorted(candidate_edges):
        edge = (predecessor_key, successor_key)
        if edge in seen:
            continue
        seen.add(edge)
        source_key = successor_key if relation == "blocked by" else predecessor_key
        source_schedule_key = primary_planned_key(row_keys_by_jira, source_key)
        source_epic = planned_epics.get(source_schedule_key)
        if predecessor_key == successor_key:
            add_dependency_review(
                planned_epics,
                source_schedule_key,
                f"Skipped self-dependency {predecessor_key} -> {successor_key}.",
            )
            audit.append(
                AuditItem(
                    "Warning",
                    "SelfDependencySkipped",
                    jira_key=source_key,
                    schedule_key=source_schedule_key,
                    issue_type="Epic" if source_epic else "",
                    summary=source_epic.summary if source_epic else "",
                    field="Dependency Review",
                    color="dependency_review",
                    message=f"Skipped self-dependency {predecessor_key} -> {successor_key}.",
                    reviewer_action="Correct the Jira blocker link.",
                    source_row=source_epic.source_row if source_epic else None,
                )
            )
            continue
        if predecessor_key not in included_keys or successor_key not in included_keys:
            missing = predecessor_key if predecessor_key not in included_keys else successor_key
            add_dependency_review(
                planned_epics,
                source_schedule_key,
                f"Missing dependency target {missing}.",
            )
            audit.append(
                AuditItem(
                    "Warning",
                    "MissingDependencyTarget",
                    jira_key=source_key,
                    schedule_key=source_schedule_key,
                    issue_type="Epic" if source_epic else "",
                    summary=source_epic.summary if source_epic else "",
                    field="Dependency Review",
                    new_value=missing,
                    color="dependency_review",
                    message=f"Dependency target '{missing}' is not an included epic.",
                    reviewer_action="Confirm the target epic is in the Jira export and meets rollup/resource rules.",
                    source_row=source_epic.source_row if source_epic else None,
                )
            )
            continue

        for predecessor_schedule_key in driving_keys_by_jira[predecessor_key]:
            for successor_schedule_key in driving_keys_by_jira[successor_key]:
                if creates_cycle(graph, predecessor_schedule_key, successor_schedule_key):
                    add_dependency_review(
                        planned_epics,
                        source_schedule_key,
                        f"Skipped circular dependency {predecessor_key} -> {successor_key}.",
                    )
                    audit.append(
                        AuditItem(
                            "Warning",
                            "CircularDependencySkipped",
                            jira_key=source_key,
                            schedule_key=source_schedule_key,
                            issue_type="Epic" if source_epic else "",
                            summary=source_epic.summary if source_epic else "",
                            field="Dependency Review",
                            color="dependency_review",
                            message=f"Skipped circular dependency {predecessor_key} -> {successor_key}.",
                            reviewer_action="Resolve the circular Jira blocker relationship.",
                            source_row=source_epic.source_row if source_epic else None,
                        )
                    )
                    continue
                graph[predecessor_schedule_key].add(successor_schedule_key)
                accepted.add((predecessor_schedule_key, successor_schedule_key))

    for predecessor_schedule_key, successor_schedule_key in sorted(accepted):
        planned_epics[successor_schedule_key].predecessors.append(predecessor_schedule_key)
        planned_epics[predecessor_schedule_key].successors.append(successor_schedule_key)

    for epic in planned_epics.values():
        if epic.drives_schedule:
            continue
        primary = planned_epics.get(epic.primary_schedule_key)
        if primary:
            add_dependency_review(
                planned_epics,
                epic.key,
                (
                    f"Reference row only. Schedule logic is driven by {primary.key} "
                    f"under fixVersion {primary.fix_version or primary.rollup_key}."
                )
            )


def build_summaries(epics: Dict[str, PlanEpic]) -> Dict[str, PlanSummary]:
    buckets: Dict[str, List[PlanEpic]] = {}
    for epic in epics.values():
        buckets.setdefault(summary_id(epic.rollup_mode, epic.rollup_key), []).append(epic)
    summaries: Dict[str, PlanSummary] = {}
    for bucket_id, children in sorted(buckets.items()):
        driving_children = [child for child in children if child.drives_schedule]
        reference_children = [child for child in children if not child.drives_schedule]
        total = round(sum(child.total_story_points for child in driving_children), 2)
        completed = round(sum(child.completed_story_points for child in driving_children), 2)
        if driving_children:
            percent_complete = calculate_percent(completed, total)
        else:
            reference_total = round(sum(child.total_story_points for child in reference_children), 2)
            reference_completed = round(sum(child.completed_story_points for child in reference_children), 2)
            percent_complete = calculate_percent(reference_completed, reference_total)
        project_keys = sorted({child.key_prefix for child in children})
        summaries[bucket_id] = PlanSummary(
            summary_id=bucket_id,
            key=children[0].rollup_key,
            name=children[0].rollup_name,
            rollup_mode=children[0].rollup_mode,
            project_key=project_keys[0] if len(project_keys) == 1 else "MULTIPLE",
            total_story_points=total,
            completed_story_points=completed,
            percent_complete=percent_complete,
            child_epic_count=len(children),
            driving_epic_count=len(driving_children),
            reference_epic_count=len(reference_children),
        )
    return summaries


def compare_with_baseline(
    epics: Dict[str, PlanEpic],
    summaries: Dict[str, PlanSummary],
    baseline: Dict[str, ProjectTaskSnapshot],
    config: Dict[str, Any],
    audit: List[AuditItem],
) -> None:
    if not baseline:
        for epic in epics.values():
            add_added_epic_audit(audit, epic, "Epic is included in the planned sandbox and was not found in the comparison baseline.")
        return

    for epic in epics.values():
        existing = baseline.get(epic.key)
        if not existing:
            add_added_epic_audit(audit, epic, "Epic is new relative to the comparison baseline.")
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


def add_added_epic_audit(audit: List[AuditItem], epic: PlanEpic, message: str) -> None:
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


def add_dependency_review(epics: Dict[str, PlanEpic], key: str, note: str) -> None:
    epic = epics.get(key)
    if not epic:
        return
    if epic.dependency_review:
        epic.dependency_review += " "
    epic.dependency_review += note


def primary_planned_key(row_keys_by_jira: Dict[str, List[str]], jira_key: str) -> str:
    keys = row_keys_by_jira.get(jira_key, [])
    return keys[0] if keys else jira_key


def creates_cycle(graph: Dict[str, Set[str]], predecessor_key: str, successor_key: str) -> bool:
    stack = [successor_key]
    seen: Set[str] = set()
    while stack:
        current = stack.pop()
        if current == predecessor_key:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, set()))
    return False


def calculate_percent(completed: float, total: float) -> int:
    if total <= 0:
        return 0
    return int(round((completed / total) * 100))


def parse_number(value: str) -> Optional[float]:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_date(value: str, audit: List[AuditItem], key: str, row_index: int) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        audit.append(
            AuditItem(
                "Warning",
                "UnparsedDate",
                jira_key=key,
                old_value=raw,
                message=f"Could not parse date '{raw}' on CSV row {row_index}.",
                reviewer_action="Use YYYY-MM-DD or configure a supported export date format.",
                source_row=row_index,
            )
        )
        return raw


def parse_issue_keys(values: Iterable[str]) -> Set[str]:
    keys: Set[str] = set()
    for value in values:
        for match in JIRA_KEY_RE.findall(value.upper()):
            keys.add(match)
    return keys


def split_multi_values(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        for part in re.split(r"[;\n,]+", value):
            cleaned = part.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def jira_key_prefix(key: str) -> str:
    return key.split("-", 1)[0].upper() if "-" in key else key.upper()


def rollup_mode_for_prefix(config: Dict[str, Any], prefix: str) -> str:
    return str(config.get("rollup_modes", {}).get(prefix.upper(), config.get("rollup_mode", "initiative")))


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
    return str(config.get("rollup_mode", "initiative"))


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def audit_to_rows(audit_items: Sequence[AuditItem]) -> List[Dict[str, Any]]:
    return [asdict(item) for item in audit_items]


def run_plan_to_state(plan: RunPlan) -> Dict[str, Any]:
    return {
        "version": 1,
        "generated_at": plan.generated_at,
        "jira_csv": plan.jira_csv,
        "rollup_mode": plan.rollup_mode,
        "epics": {key: asdict(epic) for key, epic in plan.epics.items()},
        "summaries": {key: asdict(summary) for key, summary in plan.summaries.items()},
    }


def snapshots_from_state(path: Path) -> Dict[str, ProjectTaskSnapshot]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    snapshots: Dict[str, ProjectTaskSnapshot] = {}
    for key, epic in data.get("epics", {}).items():
        snapshots[key] = ProjectTaskSnapshot(
            key=key,
            jira_key=epic.get("jira_key", key),
            name=epic.get("summary", ""),
            issue_id=epic.get("issue_id", ""),
            issue_type="Epic",
            rollup_mode=epic.get("rollup_mode", ""),
            rollup_key=epic.get("rollup_key", ""),
            resource_group=epic.get("resource_group", ""),
            key_prefix=epic.get("key_prefix", ""),
            total_story_points=float(epic.get("total_story_points") or 0),
            completed_story_points=float(epic.get("completed_story_points") or 0),
            percent_complete=int(epic.get("percent_complete") or 0),
            status=epic.get("status", ""),
            target_start=epic.get("target_start", ""),
            target_end=epic.get("target_end", ""),
            predecessors=list(epic.get("predecessors", [])),
            successors=list(epic.get("successors", [])),
            row_role=epic.get("row_role", ""),
            fix_version=epic.get("fix_version", ""),
            drives_schedule=bool(epic.get("drives_schedule", True)),
            primary_schedule_key=epic.get("primary_schedule_key", ""),
            source=str(path),
        )
    return snapshots


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))
