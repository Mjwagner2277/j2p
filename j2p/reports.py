"""Manager and audit report writers."""

from __future__ import annotations

import csv
import os
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .formatting import format_number, html_escape
from .cascade import CascadeProjection
from .schedule_drivers import build_schedule_drivers
from .metrics import calculate_story_point_ratio
from .models import AuditItem, RunPlan
from .review_focus import build_review_focus
from .rollups import build_summaries, multi_fixversion_policy_for_prefix, summary_id


AUDIT_COLUMNS = [
    "severity",
    "category",
    "jira_key",
    "schedule_key",
    "project_key",
    "issue_type",
    "summary",
    "field",
    "old_value",
    "new_value",
    "color",
    "message",
    "reviewer_action",
    "source_row",
    "source_file",
    "planning_date",
    "planning_bucket",
]

PLANNED_EPIC_COLUMNS = [
    "jira_key",
    "schedule_key",
    "project_key",
    "summary",
    "status",
    "rollup_mode",
    "rollup_key",
    "rollup_name",
    "row_role",
    "fix_version",
    "drives_schedule",
    "primary_schedule_key",
    "resource_group",
    "key_prefix",
    "total_story_points",
    "completed_story_points",
    "logged_hours",
    "completed_logged_hours",
    "story_point_ratio",
    "percent_complete",
    "in_planning",
    "completed",
    "target_start",
    "target_end",
    "predecessors",
    "successors",
    "dependency_review",
]

SUMMARY_ROLLUP_COLUMNS = [
    "rollup_key",
    "project_key",
    "name",
    "rollup_mode",
    "child_epic_count",
    "driving_epic_count",
    "reference_epic_count",
    "total_story_points",
    "completed_story_points",
    "completion_total_story_points",
    "completion_completed_story_points",
    "logged_hours",
    "completed_logged_hours",
    "story_point_ratio",
    "percent_complete",
    "target_end",
]


def write_reports(
    plan: RunPlan,
    run_dir: Path,
    config: Dict[str, Any],
    sandbox_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
) -> Dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    html_report_dir = run_dir / "reports" / "html"
    csv_report_dir = run_dir / "reports" / "csv"
    docs_dir = run_dir / "docs"
    html_report_dir.mkdir(parents=True, exist_ok=True)
    csv_report_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    by_project_key_dir = csv_report_dir / "by-project-key"
    paths = {
        "html_report": html_report_dir,
        "html_report_index": html_report_dir / "index.html",
        "manager_report": html_report_dir / "Manager-Review-Report.html",
        "resource_group_reports": html_report_dir / "resource-groups",
        "audit_detail": csv_report_dir / "audit-detail.csv",
        "planned_epics": csv_report_dir / "planned-epics.csv",
        "summary_rollups": csv_report_dir / "summary-rollups.csv",
        "dependency_review": csv_report_dir / "dependency-review.csv",
        "field_mapping": docs_dir / "FIELD_MAPPING.md",
    }

    write_audit_csv(paths["audit_detail"], plan.audit_items)
    write_planned_epics(paths["planned_epics"], plan)
    write_summary_rollups(paths["summary_rollups"], plan)
    write_dependency_review(paths["dependency_review"], plan.audit_items)
    write_field_mapping(paths["field_mapping"], config)
    write_per_project_key_csvs(by_project_key_dir, plan)
    resource_group_reports = write_resource_group_html_reports(
        paths["resource_group_reports"],
        plan,
        config,
        sandbox_path,
        state_path,
        by_project_key_dir,
    )
    write_manager_html(
        paths["manager_report"],
        plan,
        config,
        sandbox_path,
        state_path,
        by_project_key_path=by_project_key_dir,
    )
    write_html_report_index(paths["html_report_index"], paths["manager_report"], resource_group_reports)
    paths["by_project_key"] = by_project_key_dir
    return paths


def write_audit_csv(path: Path, audit_items: Sequence[AuditItem]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for item in audit_items:
            row = asdict(item)
            row["project_key"] = project_key_from_jira_key(item.jira_key)
            writer.writerow({key: row.get(key, "") for key in AUDIT_COLUMNS})


def write_planned_epics(path: Path, plan: RunPlan) -> None:
    rows = planned_epic_rows(plan)
    write_rows(path, PLANNED_EPIC_COLUMNS, rows)


def write_summary_rollups(path: Path, plan: RunPlan) -> None:
    rows = summary_rollup_rows(plan)
    write_rows(path, SUMMARY_ROLLUP_COLUMNS, rows)


def write_dependency_review(path: Path, audit_items: Sequence[AuditItem]) -> None:
    dependency_items = [
        item
        for item in audit_items
        if "Dependency" in item.category or item.field in {"Predecessors", "Successors", "Dependency Review"}
    ]
    write_audit_csv(path, dependency_items)


def write_per_project_key_csvs(base_dir: Path, plan: RunPlan) -> None:
    project_keys = sorted(
        {
            epic.key_prefix
            for epic in plan.epics.values()
            if epic.key_prefix
        }
        | {
            project_key_from_jira_key(item.jira_key)
            for item in plan.audit_items
            if project_key_from_jira_key(item.jira_key)
        }
    )
    if not project_keys:
        return

    base_dir.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for project_key in project_keys:
        project_dir = base_dir / safe_filename(project_key)
        project_dir.mkdir(parents=True, exist_ok=True)

        audit_items = [
            item for item in plan.audit_items if project_key_from_jira_key(item.jira_key) == project_key
        ]
        dependency_items = [
            item
            for item in audit_items
            if "Dependency" in item.category
            or item.field in {"Predecessors", "Successors", "Dependency Review"}
        ]
        epic_rows = [
            row for row in planned_epic_rows(plan) if row.get("project_key") == project_key
        ]
        summary_rows = summary_rollup_rows_for_project_key(plan, project_key)

        write_audit_csv(project_dir / "audit-detail.csv", audit_items)
        write_rows(project_dir / "planned-epics.csv", PLANNED_EPIC_COLUMNS, epic_rows)
        write_rows(project_dir / "summary-rollups.csv", SUMMARY_ROLLUP_COLUMNS, summary_rows)
        write_audit_csv(project_dir / "dependency-review.csv", dependency_items)

        index_rows.append(
            {
                "project_key": project_key,
                "audit_detail": str(project_dir / "audit-detail.csv"),
                "planned_epics": str(project_dir / "planned-epics.csv"),
                "summary_rollups": str(project_dir / "summary-rollups.csv"),
                "dependency_review": str(project_dir / "dependency-review.csv"),
            }
        )

    write_rows(
        base_dir / "index.csv",
        ["project_key", "audit_detail", "planned_epics", "summary_rollups", "dependency_review"],
        index_rows,
    )


def write_resource_group_html_reports(
    base_dir: Path,
    plan: RunPlan,
    config: Dict[str, Any],
    sandbox_path: Optional[Path],
    state_path: Optional[Path],
    by_project_key_path: Path,
) -> List[tuple[str, Path]]:
    base_dir.mkdir(parents=True, exist_ok=True)
    for stale_report in base_dir.glob("*.html"):
        stale_report.unlink()
    resource_groups = sorted(
        {epic.resource_group for epic in plan.epics.values() if epic.resource_group}
    )
    if not resource_groups:
        return []

    reports: List[tuple[str, Path]] = []
    used_filenames: set[str] = set()
    for resource_group in resource_groups:
        report_plan = resource_group_run_plan(plan, config, resource_group)
        report_path = base_dir / unique_report_filename(resource_group, used_filenames)
        write_manager_html(
            report_path,
            report_plan,
            config,
            sandbox_path,
            state_path,
            report_scope=f"Resource Group: {resource_group}",
            cascade_plan=plan,
            cascade_root_resource_group=resource_group,
            by_project_key_path=by_project_key_path,
        )
        reports.append((resource_group, report_path))
    return reports

def write_html_report_index(
    path: Path,
    manager_report: Path,
    resource_group_reports: Sequence[tuple[str, Path]],
) -> None:
    resource_rows = "\n".join(
        (
            "<tr>"
            f"<td>{html_escape(resource_group)}</td>"
            f"<td><a href=\"{html_escape(relative_html_path(path.parent, report_path))}\">Open report</a></td>"
            "</tr>"
        )
        for resource_group, report_path in resource_group_reports
    )
    if not resource_rows:
        resource_rows = "<tr><td colspan=\"2\">No resource-group reports were generated.</td></tr>"
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>j2p HTML Reports</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 24px 32px; color: #1f2328; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    p {{ color: #59636e; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 900px; margin-top: 16px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px 10px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    a {{ color: #0969da; }}
  </style>
</head>
<body>
  <h1>j2p HTML Reports</h1>
  <p>Open the overall manager report first, then use resource-group reports when reviewing team-specific items.</p>
  {render_manifest_link(path)}
  <table>
    <thead><tr><th>Report</th><th>Link</th></tr></thead>
    <tbody>
      <tr><td>Overall Manager Report</td><td><a href="{html_escape(relative_html_path(path.parent, manager_report))}">Open report</a></td></tr>
    </tbody>
  </table>
  <h2>Resource Group Reports</h2>
  <table>
    <thead><tr><th>Resource Group</th><th>Link</th></tr></thead>
    <tbody>{resource_rows}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def unique_report_filename(label: str, used_filenames: set[str]) -> str:
    base = safe_filename(label)
    filename = f"{base}.html"
    index = 2
    while filename in used_filenames:
        filename = f"{base}-{index}.html"
        index += 1
    used_filenames.add(filename)
    return filename


def relative_html_path(base_dir: Path, target: Path) -> str:
    try:
        return target.relative_to(base_dir).as_posix()
    except ValueError:
        return target.as_posix()


def render_manifest_link(report_path: Path) -> str:
    """Link a run's manifest when present; standalone legacy reports need none."""
    candidates = [report_path.parent / "run-manifest.json"]
    for parent in report_path.parents:
        if parent.name == "reports":
            candidates.append(parent.parent / "run-manifest.json")
            break
    for manifest in candidates:
        if manifest.is_file():
            relative = Path(os.path.relpath(manifest, report_path.parent)).as_posix()
            return f'<p class="muted"><a href="{html_escape(relative)}">Run manifest and input provenance</a></p>'
    return ""


def resource_group_run_plan(plan: RunPlan, config: Dict[str, Any], resource_group: str) -> RunPlan:
    epics = {
        key: epic
        for key, epic in plan.epics.items()
        if epic.resource_group == resource_group
    }
    summaries = build_summaries(
        epics, config, target_ends={key: summary.target_end for key, summary in plan.summaries.items()}
    )
    audit_items = [
        item for item in plan.audit_items if audit_item_resource_group(plan, item) == resource_group
    ]
    driving_epics = [epic for epic in epics.values() if epic.drives_schedule]
    completed_points = round(sum(epic.completed_story_points for epic in driving_epics), 2)
    completed_logged_hours = round(sum(epic.completed_logged_hours for epic in driving_epics), 2)
    stats = dict(plan.stats)
    stats.update(
        {
            "epics_included": len({epic.jira_key or epic.key for epic in epics.values()}),
            "planned_epic_rows": len(epics),
            "summary_rows": len(summaries),
            "audit_items": len(audit_items),
            "project_keys": sorted({epic.key_prefix for epic in epics.values() if epic.key_prefix}),
            "logged_hours": round(sum(epic.logged_hours for epic in driving_epics), 2),
            "completed_logged_hours": completed_logged_hours,
            "story_point_ratio": calculate_story_point_ratio(
                completed_logged_hours,
                completed_points,
                float(plan.stats.get("hours_per_story_point", 8.0)),
            ),
        }
    )
    return RunPlan(
        generated_at=plan.generated_at,
        jira_csv=plan.jira_csv,
        rollup_mode=plan.rollup_mode,
        column_map=plan.column_map,
        stats=stats,
        summaries=summaries,
        epics=epics,
        audit_items=audit_items,
    )


def completed_fixversion_report_plan(plan: RunPlan) -> RunPlan:
    suppressed_ids = set(plan.stats.get("suppressed_completed_fixversion_summary_ids", []))
    suppressed_ids &= set(plan.summaries)
    if not suppressed_ids:
        return plan

    epics = {
        key: epic
        for key, epic in plan.epics.items()
        if summary_id(epic.rollup_mode, epic.rollup_key) not in suppressed_ids
    }
    summaries = {
        key: summary
        for key, summary in plan.summaries.items()
        if key not in suppressed_ids
    }
    audit_items = [
        item
        for item in plan.audit_items
        if item.severity == "Error"
        or not audit_item_matches_suppressed_fixversion(plan, item, suppressed_ids)
    ]

    driving_epics = [epic for epic in epics.values() if epic.drives_schedule]
    completed_points = round(sum(epic.completed_story_points for epic in driving_epics), 2)
    completed_logged_hours = round(sum(epic.completed_logged_hours for epic in driving_epics), 2)
    hidden_epic_rows = len(plan.epics) - len(epics)
    stats = dict(plan.stats)
    stats.update(
        {
            "epics_included": len({epic.jira_key or epic.key for epic in epics.values()}),
            "planned_epic_rows": len(epics),
            "summary_rows": len(summaries),
            "audit_items": len(audit_items),
            "project_keys": sorted({epic.key_prefix for epic in epics.values() if epic.key_prefix}),
            "logged_hours": round(sum(epic.logged_hours for epic in driving_epics), 2),
            "completed_logged_hours": completed_logged_hours,
            "story_point_ratio": calculate_story_point_ratio(
                completed_logged_hours,
                completed_points,
                float(plan.stats.get("hours_per_story_point", 8.0)),
            ),
            "suppressed_completed_fixversion_summary_ids": sorted(suppressed_ids),
            "suppressed_completed_fixversion_rollups": len(suppressed_ids),
            "suppressed_completed_fixversion_epic_rows": hidden_epic_rows,
            "multi_fixversion_epics": len(
                {
                    epic.jira_key or epic.key
                    for epic in epics.values()
                    if epic.row_role in {"Primary", "Reference", "Split"}
                }
            ),
        }
    )
    return RunPlan(
        generated_at=plan.generated_at,
        jira_csv=plan.jira_csv,
        rollup_mode=plan.rollup_mode,
        column_map=plan.column_map,
        stats=stats,
        summaries=summaries,
        epics=epics,
        audit_items=audit_items,
    )


def audit_item_matches_suppressed_fixversion(
    plan: RunPlan,
    item: AuditItem,
    suppressed_ids: set[str],
) -> bool:
    if item.category == "SuppressedCompletedFixVersion":
        return True
    if item.schedule_key in suppressed_ids:
        return True
    for key in (item.schedule_key, item.jira_key):
        epic = epic_for_key(plan, key)
        if epic and summary_id(epic.rollup_mode, epic.rollup_key) in suppressed_ids:
            return True
    return False


def audit_item_resource_group(plan: RunPlan, item: AuditItem) -> str:
    for key in (item.schedule_key, item.jira_key):
        epic = epic_for_key(plan, key)
        if epic:
            return epic.resource_group
    context = plan.stats.get("review_issue_context", {}).get(item.jira_key, {})
    return context.get("resource_group", "")


def epic_for_key(plan: RunPlan, key: str) -> Any:
    if not key:
        return None
    normalized_key = key.upper()
    direct = plan.epics.get(normalized_key)
    if direct:
        return direct
    for epic in plan.epics.values():
        if (epic.jira_key or epic.key).upper() == normalized_key:
            return epic
    return None


def planned_epic_rows(plan: RunPlan) -> List[Dict[str, Any]]:
    rows = []
    for epic in sorted(plan.epics.values(), key=lambda item: (item.rollup_mode, item.rollup_key, item.key)):
        row = asdict(epic)
        row["schedule_key"] = row.pop("key")
        row["jira_key"] = epic.jira_key or epic.key
        row["project_key"] = epic.key_prefix
        row["predecessors"] = ",".join(epic.predecessors)
        row["successors"] = ",".join(epic.successors)
        row["drives_schedule"] = "Yes" if epic.drives_schedule else "No"
        row["in_planning"] = "Yes" if epic.in_planning else "No"
        row["completed"] = "Yes" if epic.completed else "No"
        row["story_point_ratio"] = epic.story_point_ratio
        rows.append({key: row.get(key, "") for key in PLANNED_EPIC_COLUMNS})
    return rows


def summary_rollup_rows(plan: RunPlan) -> List[Dict[str, Any]]:
    rows = []
    for summary in sorted(plan.summaries.values(), key=lambda item: (item.rollup_mode, item.key)):
        row = asdict(summary)
        row["rollup_key"] = row.pop("key")
        row["story_point_ratio"] = summary.story_point_ratio
        rows.append({key: row.get(key, "") for key in SUMMARY_ROLLUP_COLUMNS})
    return rows


def summary_rollup_rows_for_project_key(plan: RunPlan, project_key: str) -> List[Dict[str, Any]]:
    epics = {key: epic for key, epic in plan.epics.items() if epic.key_prefix == project_key}
    summaries = build_summaries(epics, {"metrics": {
        "hours_per_story_point": float(plan.stats.get("hours_per_story_point", 8.0)),
    }}, target_ends={key: summary.target_end for key, summary in plan.summaries.items()})
    rows = []
    for summary in summaries.values():
        row = asdict(summary)
        row['rollup_key'] = row.pop('key')
        rows.append({key: row.get(key, '') for key in SUMMARY_ROLLUP_COLUMNS})
    return rows


def write_rows(path: Path, columns: Sequence[str], rows: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def write_field_mapping(path: Path, config: Dict[str, Any]) -> None:
    lines = [
        "# j2p Microsoft Project Field Mapping",
        "",
        "These Project task fields are used by j2p when it creates or updates a sandbox Project file.",
        "",
        "Native Project fields used by j2p:",
        "",
        "| j2p Value | Project Field | Purpose |",
        "| --- | --- | --- |",
        "| Resource Group | Resource Group | Primary tasks only: assigned from the Jira key prefix mapping. Active copies have no resource assignments. |",
        "| Scheduled dates | Start / Finish | Native primary schedule, synchronized active-copy dates, and automatically calculated summary dates. |",
        "",
        "Custom task fields used by j2p:",
        "",
        "| j2p Value | Project Field | Project Column Name |",
        "| --- | --- | --- |",
    ]
    project_fields = config.get("project_fields", {})
    project_field_names = config.get("project_field_names", {})
    for key in sorted(project_fields):
        if key in {"schedule_start", "schedule_finish"}:
            continue  # Retired custom snapshots are not current schedule fields.
        lines.append(f"| `{key}` | `{project_fields[key]}` | {project_field_names.get(key, key)} |")
    lines.extend(
        [
            "",
            "Review table visibility:",
            "",
            f"- Exposed columns: {format_review_table_exposed_columns(config)}",
            f"- Auto-include changed/review columns: {format_bool(config.get('review_table', {}).get('include_audit_columns', True))}",
            "",
            "Color key:",
            "",
            "- Green: changed cell, including autoscheduled Start/Finish",
            "- Red: schedule driver cards in the report diagram only; Project date cells remain green",
            "- Yellow/amber: unmatched or manager review needed",
            "- Light gray: dependency review marker",
            "- Gray/green-gray: in planning",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def format_review_table_exposed_columns(config: Dict[str, Any]) -> str:
    exposed_columns = config.get("review_table", {}).get("exposed_columns", "all")
    if exposed_columns == "all":
        return "`all`"
    return ", ".join(f"`{column}`" for column in exposed_columns)


def format_bool(value: Any) -> str:
    return "yes" if bool(value) else "no"


def write_manager_html(
    path: Path,
    plan: RunPlan,
    config: Dict[str, Any],
    sandbox_path: Optional[Path],
    state_path: Optional[Path],
    report_scope: str = "Overall Manager Report",
    cascade_plan: Optional[RunPlan] = None,
    cascade_root_resource_group: Optional[str] = None,
    by_project_key_path: Optional[Path] = None,
) -> None:
    # Completed upstream work can still explain unfinished successor movement.
    cascade_display_plan = cascade_plan or plan
    plan = completed_fixversion_report_plan(plan)
    review_settings = config.get("report_review", {})
    focus_days = int(review_settings.get("focus_days", 90))
    focus = build_review_focus(plan, days=focus_days, dependency_plan=cascade_plan or plan)
    html_dir = path.parent.parent if path.parent.name == "resource-groups" else path.parent
    csv_dir = by_project_key_path.parent if by_project_key_path else html_dir.parent / "csv"

    drivers = render_schedule_cascade_review(
        cascade_display_plan,
        project_update_run=sandbox_path is not None,
        root_resource_group=cascade_root_resource_group,
        include_heading=False,
    )
    rollups = render_completion_summary(plan) + render_rollup_status(plan, compact=True)
    rollups += render_csv_links(path, csv_dir, [
        ("summary-rollups.csv", "Full rollup CSV"),
        ("planned-epics.csv", "Planned epic CSV"),
    ])
    reviews = render_review_focus(focus, focus_days, int(review_settings.get("max_focus_items", 25)))
    reviews += render_csv_links(path, csv_dir, [
        ("audit-detail.csv", "Full audit CSV"),
        ("dependency-review.csv", "Dependency review CSV"),
    ])
    sections = (
        render_collapsible("Cascading Schedule Drivers", drivers)
        + render_collapsible("Rollup and Completion", rollups)
        + render_collapsible("Items for Review", reviews,
                             f"{focus['focus_count']} priority fixes; {focus['grouped_count']} grouped items total")
    )
    styles = f""":root {{
      --changed: {html_escape(config['colors']['changed_cell'])};
      --cascade: {html_escape(config['colors']['cascade_root'])};
      --border: #d0d7de;
      --text: #1f2328;
      --muted: #59636e;
      --section: #f6f8fa;
    }}""" + """
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--text); background: white;
           font: 14px/1.5 Arial, Helvetica, sans-serif; }
    header, main { max-width: 1200px; margin: auto; padding: 20px 24px; }
    header { padding-bottom: 0; }
    h1 { margin: 0; font-size: 24px; }
    h2 { margin: 16px 0 8px; font-size: 16px; }
    p { margin: 8px 0; }
    a { color: #245b92; }
    .muted, .empty, .summary-note, .cascade-help,
    .cascade-reference, .cascade-limit, .cascade-node-dates,
    .cascade-branch-count { color: var(--muted); }
    .summary-note, .cascade-node-dates, .cascade-branch-count { font-size: 12px; font-weight: normal; }
    .summary-note { display: block; margin-top: 3px; }
    details { min-width: 0; border: 1px solid var(--border); border-radius: 6px;
              margin: 12px 0; background: white; }
    summary { cursor: pointer; padding: 12px 14px; font-weight: bold;
              background: var(--section); overflow-wrap: anywhere; }
    summary:focus-visible { outline: 2px solid #245b92; outline-offset: 2px; }
    details[open] > summary { border-bottom: 1px solid var(--border); }
    .detail-body, .cascade-branch-body { padding: 0 14px 14px; min-width: 0; }
    .table-wrap { width: 100%; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; font-size: 13px; }
    th, td { border: 1px solid var(--border); padding: 7px 8px; text-align: left;
             vertical-align: top; overflow-wrap: anywhere; }
    th { background: var(--section); }
    .completion-summary { display: flex; flex-wrap: wrap; gap: 12px 28px; margin: 14px 0; }
    .completion-summary p { margin: 0; }
    .completion-summary strong { display: block; font-size: 20px; }
    .csv-links { font-size: 12px; }
    .cascade-flow { overflow-x: auto; }
    .cascade-branch-count { display: block; }
    .cascade-branch-body { padding-top: 12px; }
    .cascade-node { min-width: 180px; padding: 8px 10px; border: 1px solid var(--border);
                    border-left: 4px solid var(--changed); border-radius: 4px; }
    .cascade-node.driver { border-left-color: var(--cascade); }
    .cascade-node-title { font-weight: bold; overflow-wrap: anywhere; }
    .cascade-node-title span { color: var(--muted); font-size: 12px; font-weight: normal; }
    .cascade-node-summary { overflow-wrap: anywhere; }
    .cascade-children { border-left: 1px solid var(--border); margin: 8px 0 0 8px;
                        padding-left: 8px; display: grid; gap: 8px; }
    @media (max-width: 600px) {
      header, main { padding-left: 12px; padding-right: 12px; }
      .detail-body, .cascade-branch-body { padding-left: 8px; padding-right: 8px; }
      th, td { padding: 6px; }
    }
    """
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape(report_title(report_scope))}</title>
  <style>{styles}</style>
</head>
<body>
  <header>
    <h1>{html_escape(report_title(report_scope))}</h1>
    <p class="muted">Generated {html_escape(plan.generated_at)} · Scope: {html_escape(report_scope)}</p>
  </header>
  <main>{sections}</main>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def render_csv_links(report_path: Path, csv_dir: Path, files: Sequence[tuple[str, str]]) -> str:
    links = []
    for filename, label in files:
        relative = Path(os.path.relpath(csv_dir / filename, report_path.parent)).as_posix()
        links.append(f'<a href="{html_escape(relative)}">{html_escape(label)}</a>')
    return '<p class="csv-links">' + ' · '.join(links) + '</p>'


def render_completion_summary(plan: RunPlan) -> str:
    # Count each scheduled row once; reference membership remains in rollups.
    epics = [epic for epic in plan.epics.values() if epic.drives_schedule]
    total = round(sum(epic.total_story_points for epic in epics), 2)
    done = round(sum(epic.completed_story_points for epic in epics), 2)
    completion = f"{format_number(round(100 * done / total, 1))}%" if total else "Not estimated"
    completed = sum(epic.completed for epic in epics)
    return (
        '<div class="completion-summary">'
        f'<p>Completed / Total Points<strong>{format_number(done)} / {format_number(total)}</strong></p>'
        f'<p>Point Completion<strong>{html_escape(completion)}</strong></p>'
        f'<p>Completed / Total Epics<strong>{completed} / {len(epics)}</strong></p>'
        '</div><p class="muted">Visible scheduled work; reference rows are excluded from these totals. '
        'Each rollup includes completion credit from all its members.</p>'
    )


def report_title(report_scope: str) -> str:
    if report_scope == "Overall Manager Report":
        return "Schedule Review Report"
    return f"Schedule Review Report - {report_scope}"


def decision_briefing(plan: RunPlan, focus: Optional[Dict[str, Any]] = None) -> str:
    focus = focus if focus is not None else build_review_focus(plan)
    completed_items = by_category(plan.audit_items, "CompletedSinceLastUpdate")
    in_progress_rollups = sum(1 for summary in plan.summaries.values() if 0 < summary.percent_complete < 100)
    completed_rollups = sum(1 for summary in plan.summaries.values() if summary.percent_complete >= 100)
    ratio_summary = project_wide_story_point_ratio_summary(plan)
    metrics = [
        ("Focus Now", focus["focus_count"], "Grouped fixes, ordered by impact"),
        ("Review Entries", focus["total_audit_count"], f"{focus['grouped_count']} grouped actions, including future planning"),
        ("Rollups In Progress", in_progress_rollups, f"{completed_rollups} complete"),
        ("Later / Historical", f"{focus['later_count']} / {focus['historical_count']}", "Grouped actions available below"),
        ("Unscheduled Work", focus["unscheduled_count"], "Grouped actions without Jira target dates; expand below"),
        ("Completed Epics", len(completed_items), "Completed since comparison baseline"),
        ("Logged Hours", format_number(plan.stats.get("logged_hours", 0)), "Rolled up from child work"),
        (
            story_point_ratio_label(plan),
            format_number(ratio_summary["story_point_ratio"]),
            pluralize(ratio_summary["epic_count"], "in-progress row", "in-progress rows"),
        ),
    ]
    suppressed_count = int(plan.stats.get("suppressed_audit_items", 0) or 0)
    if suppressed_count:
        metrics.append(
            (
                "Historical Items Suppressed",
                suppressed_count,
                "Before configured warning cutoff",
            )
        )
    hidden_fixversion_count = int(plan.stats.get("suppressed_completed_fixversion_rollups", 0) or 0)
    if hidden_fixversion_count:
        metrics.append(
            (
                "Completed FixVersions Hidden",
                hidden_fixversion_count,
                f"{plan.stats.get('suppressed_completed_fixversion_epic_rows', 0)} completed row(s) omitted",
            )
        )
    return f"<section><h2>Decision Briefing</h2><div class=\"briefing-grid\">{render_metric_cards(metrics)}</div></section>"



def render_review_focus(focus: Dict[str, Any], days: int, limit: int) -> str:
    groups = focus["groups"]
    current = [group for group in groups if group["tier"] in {"Fix first", "Focus now"}]
    later = [group for group in groups if group["tier"] == "Later"]
    historical = [group for group in groups if group["tier"] == "Historical"]
    unscheduled = [group for group in groups if group["tier"] == "Unscheduled"]
    window = f"the next {days} days" if days else "all dated unfinished work"
    intro = (
        f'<p class="muted">Prioritized fixes for {html_escape(window)}, overdue work, '
        'and serious errors. Undated work stays in Unscheduled Work; Project auto-scheduled '
        'dates do not promote it into this priority list.</p>'
    )
    parts = [intro, render_focus_group_table("Highest Priority Fixes", current[:limit])]
    if len(current) > limit:
        parts.append(render_collapsible(
            "More Current Fixes", render_focus_group_table("Remaining Current Fixes", current[limit:]),
            f"{len(current) - limit} more grouped actions, in priority order.",
        ))
    if later:
        parts.append(render_collapsible(
            "Later Work", render_focus_group_table("Future Fixes", later),
            f"{len(later)} grouped actions beyond the focus window.",
        ))
    if unscheduled:
        parts.append(render_collapsible(
            "Unscheduled Work", render_focus_group_table("Undated Work Review", unscheduled),
            f"{len(unscheduled)} grouped actions without Jira target dates; retained outside the priority list.",
        ))
    if historical:
        parts.append(render_collapsible(
            "Historical Cleanup", render_focus_group_table("Completed Work Review", historical),
            f"{len(historical)} grouped actions on completed work, retained for review.",
        ))
    return "".join(parts)


def render_focus_group_table(title: str, groups: Sequence[Dict[str, Any]]) -> str:
    rows = [[
        group["key"], group["summary"], group["target_end"] or "Not set",
        group["downstream_count"], "; ".join(group["reasons"]), " ".join(group["actions"]),
    ] for group in groups]
    return render_table(title, [
        "Item", "Summary", "Target End", "Downstream Tasks", "Why Review", "Next Action",
    ], rows)


def render_metric_cards(metrics: Sequence[Sequence[Any]]) -> str:
    cards = "\n".join(
        (
            "<div class=\"briefing-item\">"
            f"<span>{html_escape(label)}</span>"
            f"<strong>{html_escape(value)}</strong>"
            f"<p class=\"muted\">{html_escape(note)}</p>"
            "</div>"
        )
        for label, value, note in metrics
    )
    return cards


def render_schedule_cascade_review(
    plan: RunPlan,
    project_update_run: bool = False,
    root_resource_group: Optional[str] = None,
    include_heading: bool = True,
) -> str:
    """One driver view; general date comparisons remain in the CSV audit."""
    model = build_schedule_drivers(plan, root_resource_group)
    heading = '<section>' + ('<h2>Schedule Drivers</h2>' if include_heading else '')
    if not model["roots"]:
        if project_update_run or model["changes"]:
            message = "No changed schedule drivers affecting unfinished dated work were found in this run."
        else:
            message = "Schedule drivers are evaluated after Project scheduling during create/update runs."
        return heading + f'<p class="empty">{html_escape(message)}</p></section>'

    projection = CascadeProjection(model["graph"])
    branches = []
    for root in model["roots"]:
        if projection.entries >= projection.max_entries:
            projection.entry_limited = True
            break
        branches.append(render_schedule_driver_branch(plan, model, root, projection))
    limit_notice = ""
    if projection.entry_limited or projection.depth_limited:
        limit_notice = (
            '<p class="cascade-limit">Visual tree shortened to keep this report responsive. '
            'All date evidence remains in audit-detail.csv, linked under Items for Review.</p>'
        )
    return (
        heading
        + '<p class="cascade-help">Finish changes linked to changed successor dates, '
        'ordered by affected unfinished tasks with Jira dates. Expand a driver to review the dependency chain. '
        'Linked changes identify possible schedule impact; they do not prove causation.</p>'
        + f'{limit_notice}<div class="cascade-flow">'
        + "".join(branches)
        + '</div></section>'
    )


def render_schedule_driver_branch(
    plan: RunPlan, model: Dict[str, Any], root: str, projection: CascadeProjection,
) -> str:
    item = model["changes"][root]["Finish"]
    epic = epic_for_key(plan, root)
    jira_key = item.jira_key or (epic.jira_key if epic else "") or root
    summary = item.summary or (epic.summary if epic else "")
    count = model["graph"].downstream_counts[root]
    return (
        '<details class="cascade-branch"><summary>'
        f'<span>{html_escape(jira_key)}: {html_escape(summary)}'
        f'<br><span class="cascade-node-dates">Finish: {html_escape(item.old_value)} '
        f'&rarr; {html_escape(item.new_value)}</span></span>'
        f'<span class="cascade-branch-count">{html_escape(pluralize(count, "affected task"))}</span>'
        '</summary><div class="cascade-branch-body">'
        + render_schedule_driver_nodes(plan, model, root, projection)
        + '</div></details>'
    )


def render_schedule_driver_nodes(
    plan: RunPlan, model: Dict[str, Any], root: str, projection: CascadeProjection,
) -> str:
    fragments = []
    for action, key in projection.events(root):
        if action == "open":
            fragments.append('<div class="cascade-children">')
            continue
        if action == "close":
            fragments.append('</div>')
            continue
        if action in {"limit", "depth"}:
            fragments.append('<p class="cascade-limit">Further date evidence remains in audit-detail.csv.</p>')
            continue
        changes = model["changes"][key]
        item = changes.get("Finish") or changes["Start"]
        epic = epic_for_key(plan, key)
        jira_key = item.jira_key or (epic.jira_key if epic else "") or key
        if action == "reference":
            fragments.append(f'<p class="cascade-reference">Shared task {html_escape(jira_key)}: already shown above.</p>')
            continue
        is_driver = key in model["driver_keys"]
        is_context = epic and (epic.completed or not (epic.target_start or epic.target_end))
        label = "Driver" if is_driver else "Context" if is_context else "Affected"
        summary = item.summary or (epic.summary if epic else "")
        node = (
            f'<div class="cascade-node {"driver" if is_driver else "changed"}">'
            f'<div class="cascade-node-title">{html_escape(jira_key)} <span>{label}</span></div>'
            f'<div class="cascade-node-summary">{html_escape(summary)}</div>'
        )
        for field in ("Start", "Finish"):
            change = changes.get(field)
            if change:
                node += (
                    f'<div class="cascade-node-dates">{field}: {html_escape(change.old_value)} '
                    f'&rarr; {html_escape(change.new_value)}</div>'
                )
        if is_context and not is_driver:
            node += '<div class="cascade-node-dates">Context only; excluded from the affected-task count.</div>'
        fragments.append(node + '</div>')
    return "".join(fragments)


def render_story_point_ratio_breakdown(plan: RunPlan) -> str:
    summary = project_wide_story_point_ratio_summary(plan)
    metrics = [
        (
            story_point_ratio_label(plan),
            format_number(summary["story_point_ratio"]),
            "In-progress scheduled epic rows only",
        ),
        (
            "In-Progress Epic Rows",
            summary["epic_count"],
            "Driving rows with 1-99% complete",
        ),
        (
            "Completed / Total Points",
            (
                f"{format_number(summary['completed_story_points'])} / "
                f"{format_number(summary['total_story_points'])}"
            ),
            "Only rows counted in this view",
        ),
        (
            "Completed Logged Hours",
            format_number(summary["completed_logged_hours"]),
            f"Expected {format_number(summary['expected_completed_hours'])}",
        ),
        (
            "Total Logged Hours",
            format_number(summary["logged_hours"]),
            "All child logs under active epics",
        ),
        (
            "Configured Hours per Point",
            format_number(summary["hours_per_story_point"]),
            "Configured in YAML",
        ),
    ]
    section = (
        f"<section><h2>{html_escape(story_point_ratio_label(plan))}</h2>"
        f"<div class=\"briefing-grid\">{render_metric_cards(metrics)}</div>"
        "<p class=\"muted\">"
        f"A value of 1.00 means one completed story point per configured "
        f"{html_escape(format_number(summary['hours_per_story_point']))}-hour block. "
        "Higher values mean more completed points per logged-time block; lower values mean fewer. "
        "This section excludes completed, not-started, in-planning, and reference-only rows."
        "</p></section>"
    )
    resource_rows = resource_group_story_point_ratio_rows(plan)
    table_rows = [
        [
            row["resource_group"],
            row["project_keys"],
            row["epic_count"],
            f"{format_number(row['completed_story_points'])} / {format_number(row['total_story_points'])}",
            format_number(row["completed_logged_hours"]),
            format_number(row["expected_completed_hours"]),
            format_number(row["logged_hours"]),
            format_number(row["story_point_ratio"]),
        ]
        for row in resource_rows
    ]
    resource_table = render_table(
        "Resource Group Story Point Ratio",
        [
            "Resource Group",
            "Project Keys",
            "In-Progress Rows",
            "Completed / Total Points",
            "Completed Logged Hours",
            "Expected Completed Hours",
            "Total Logged Hours",
            story_point_ratio_label(plan),
        ],
        table_rows,
    )
    group_count = len(resource_rows)
    summary_note = (
        pluralize(group_count, "resource group with in-progress work.", "resource groups with in-progress work.")
        if group_count
        else "No in-progress scheduled epic rows found."
    )
    return section + render_collapsible("Story Point Ratio By Resource Group", resource_table, summary_note)


def project_wide_story_point_ratio_summary(plan: RunPlan) -> Dict[str, Any]:
    return story_point_ratio_summary(report_story_point_ratio_epics(plan), plan)


def resource_group_story_point_ratio_rows(plan: RunPlan) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Any]] = {}
    for epic in report_story_point_ratio_epics(plan):
        buckets.setdefault(epic.resource_group or "Unassigned", []).append(epic)

    rows = []
    for resource_group, epics in sorted(buckets.items()):
        summary = story_point_ratio_summary(epics, plan)
        summary["resource_group"] = resource_group
        summary["project_keys"] = ", ".join(sorted({epic.key_prefix for epic in epics if epic.key_prefix}))
        rows.append(summary)
    return rows


def report_story_point_ratio_epics(plan: RunPlan) -> List[Any]:
    return [
        epic
        for epic in plan.epics.values()
        if epic.drives_schedule and 0 < epic.percent_complete < 100
    ]


def story_point_ratio_summary(epics: Iterable[Any], plan: RunPlan) -> Dict[str, Any]:
    epic_list = list(epics)
    hours_per_story_point = float(plan.stats.get("hours_per_story_point", 8.0))
    completed_story_points = round(sum(epic.completed_story_points for epic in epic_list), 2)
    total_story_points = round(sum(epic.total_story_points for epic in epic_list), 2)
    logged_hours = round(sum(epic.logged_hours for epic in epic_list), 2)
    completed_logged_hours = round(sum(epic.completed_logged_hours for epic in epic_list), 2)
    expected_completed_hours = round(completed_story_points * hours_per_story_point, 2)
    return {
        "epic_count": len(epic_list),
        "total_story_points": total_story_points,
        "completed_story_points": completed_story_points,
        "logged_hours": logged_hours,
        "completed_logged_hours": completed_logged_hours,
        "expected_completed_hours": expected_completed_hours,
        "story_point_ratio": calculate_story_point_ratio(
            completed_logged_hours,
            completed_story_points,
            hours_per_story_point,
        ),
        "hours_per_story_point": hours_per_story_point,
    }


def pluralize(count: int, singular: str, plural: Optional[str] = None) -> str:
    label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {label}"


def story_point_ratio_label(plan: RunPlan) -> str:
    return "Story Point Ratio"


def render_report_context(
    plan: RunPlan,
    sandbox_path: Optional[Path],
    state_path: Optional[Path],
    by_project_key_path: Path,
    report_scope: str = "Overall Manager Report",
) -> str:
    rows = [
        ["Report Scope", report_scope],
        ["Jira CSV", plan.jira_csv],
        ["Sandbox Project File", str(sandbox_path) if sandbox_path else "not created in validate mode"],
        ["State File", str(state_path) if state_path else "not written"],
        ["Per-Project-Key CSV Folder", str(by_project_key_path)],
        ["CSV Rows Read", plan.stats.get("csv_rows_read", 0)],
        ["Jira Issues Read", plan.stats.get("jira_issues_read", 0)],
        ["Epics Included", plan.stats.get("epics_included", 0)],
        ["Epics Excluded", plan.stats.get("epics_excluded", 0)],
        ["Planned Epic Rows", plan.stats.get("planned_epic_rows", plan.stats.get("epics_included", 0))],
        ["Summary Rollup Rows", plan.stats.get("summary_rows", 0)],
        ["Project Keys", ", ".join(plan.stats.get("project_keys", []))],
        ["Multi-FixVersion Epics", plan.stats.get("multi_fixversion_epics", 0)],
        ["Completed FixVersions Hidden", plan.stats.get("suppressed_completed_fixversion_rollups", 0)],
        ["Completed FixVersion Rows Hidden", plan.stats.get("suppressed_completed_fixversion_epic_rows", 0)],
    ]
    return render_collapsible(
        "Report Context",
        render_table("Run Inputs And Counts", ["Item", "Value"], rows)
        + render_project_update_metrics(plan),
        "Open for file paths, CSV row counts, processing totals, and available Project update measurements.",
    )


def render_project_update_metrics(plan: RunPlan) -> str:
    writes = plan.stats.get("project_update_writes", {})
    seconds = plan.stats.get("project_update_seconds", {})
    row_seconds = plan.stats.get("project_row_seconds", {})
    if not writes and not seconds and not row_seconds:
        return ""

    parts = [
        '<p class="muted">Project measurements cover the entire run, '
        "including in filtered resource-group reports. Counts are write decisions "
        "and operations, not unique fields or tasks. Skipped operations already "
        "matched the requested state or required no new scheduling seed. Full "
        "comparisons and save/reopen verification still run; these counts do not "
        "limit the audit detail.</p>"
    ]
    if writes:
        labels = {
            "task_fields": "Task field assignments",
            "resource_fields": "Resource field assignments",
            "custom_field_names": "Custom field names",
            "dependency_sets": "Dependency sets",
            "resource_assignments": "Resource assignment additions/removals",
        }
        rows = [
            [labels.get(category, category), counts.get("written", 0),
             counts.get("skipped", 0), counts.get("failed", 0)]
            for category, counts in writes.items()
        ]
        parts.append(render_table(
            "Project Update Operations (Entire Run)",
            ["Operation", "Written", "Skipped", "Failed"], rows,
        ))
    if seconds:
        creating = plan.stats.get("project_run_mode") == "create"
        labels = {
            "new": "Create blank Project",
            "initial_recalculate": "Initial Project recalculation",
            "initial_save": "Initial Save As",
            "dependencies": "Write and verify predecessor links",
            "open": "Open sandbox",
            "read_before": "Read existing Project values",
            "build_comparison": "Build complete comparison",
            "configure_fields": "Configure custom fields",
            "apply_changes": "Create Project rows" if creating else "Apply changes and prepare dependencies",
            "recalculate": "Final Project recalculation",
            "schedule_review": "Format review: schedule changes (subset)",
            "review_index": "Format review: task indexing (subset)",
            "review_duration": "Format review: undated task durations (subset)",
            "review_candidates": "Format review: color candidates (subset)",
            "review_columns": "Format review: visible columns (subset)",
            "format_review": "Format review",
            "save": "Save Project",
            "verify_save_reopen": "Verify, close, reopen, and verify",
            "total": "Total Project creation" if creating else "Total Project update",
        }
        rows = [[labels.get(phase, phase), f"{elapsed:.3f}"] for phase, elapsed in seconds.items()]
        parts.append(render_table(
            "Project Creation Timing (Entire Run)" if creating else "Project Update Timing (Entire Run)",
            ["Phase", "Seconds"], rows,
        ))
        parts.append(
            '<p class="muted">Create Project rows includes the quarter-point calculation checkpoints. '
            'Predecessor links, saving, formatting, and saved-file verification are measured separately. '
            'Total Project creation includes session startup and shutdown; CSV analysis and report generation '
            'are excluded.</p>' if creating else
            '<p class="muted">Apply changes includes the required recalculation '
            "before dependency writes. Final Project recalculation measures the "
            "postwrite pass. Total Project update includes session startup and "
            "shutdown; it excludes initial CSV preflight, sandbox copying, and "
            "HTML/CSV report generation.</p>"
        )
        parts.append(
            '<p class="muted">Task indexing, schedule-change review, undated-task duration checks, '
            'color-candidate preparation, and visible-column resolution are subsets of Format review; '
            'do not add them to its total again. Schedule-change review reuses the task index '
            'and reads only scheduled Start/Finish dates.</p>'
        )
    if row_seconds:
        labels = {
            "placement": ("Row creation/placement and summary fields"
                          if plan.stats.get("project_run_mode") == "create" else "Epic row placement"),
            "values_and_resources": "Epic fields, dates, and resources",
            "resources_within_values": "Resource assignment (included in epic values above)",
            "checkpoint_calculation": "Quarter-point calculations",
            "total": "Total epic row phase",
        }
        parts.append(render_table("Project Row Timing (Entire Run)", ["Operation", "Seconds"], [
            [labels.get(name, name), f"{value:.3f}"] for name, value in row_seconds.items()
        ]))
        parts.append('<p class="muted">Resource time is part of epic-value time; do not add it again. '
                     'These timings describe the entire run, including in filtered resource-group reports.</p>')
    return "".join(parts)


def render_rollup_status(plan: RunPlan, compact: bool = False) -> str:
    rows = []
    # Tie the assessment to the report run, so reopening an HTML report does
    # not silently change its due-date interpretation.
    as_of = date.fromisoformat(plan.generated_at[:10]).isoformat()
    for summary in sorted(plan.summaries.values(), key=lambda item: (
        not bool(item.target_end), item.target_end, item.rollup_mode, item.key
    )):
        # This is a presentation filter: unpointed rollups remain in the full
        # plan, Project, state, and CSV audit. Use completion points so a
        # reference-only version with estimated work stays visible.
        if summary.completion_total_story_points <= 0:
            continue
        rows.append(
            [
                summary.name,
                summary.key,
                summary.project_key,
                summary.rollup_mode,
                rollup_status(summary),
                summary.target_end or "Not set",
                "Past due" if summary.target_end and summary.target_end < as_of else (
                    "Due today" if summary.target_end == as_of else "Upcoming" if summary.target_end else "Not set"
                ),
                f"{summary.percent_complete}%",
                f"{format_number(summary.completion_completed_story_points)} / {format_number(summary.completion_total_story_points)}",
                f"{format_number(summary.completed_story_points)} / {format_number(summary.total_story_points)}",
                format_number(summary.logged_hours),
                format_number(summary.story_point_ratio),
                summary.driving_epic_count,
                summary.reference_epic_count,
                summary.child_epic_count,
            ]
        )
    if compact:
        columns = [0, 1, 3, 4, 5, 6, 7, 8]
        return render_table("Rollup Status", [
            "Rollup", "Rollup Key", "Mode", "Status", "Target End", "Due Status",
            "% Complete", "Completion Points (Done / Total)",
        ], [[row[index] for index in columns] for row in rows])
    return render_table(
        "Rollup Status",
        [
            "Rollup",
            "Rollup Key",
            "Project Key",
            "Mode",
            "Status",
            "Target End",
            "Due Status",
            "% Complete",
            "Completion Points (Done / Total)",
            "Counted Points (Done / Total)",
            "Logged Hours",
            story_point_ratio_label(plan),
            "Driving Rows",
            "Reference Rows",
            "Total Rows",
        ],
        rows,
    )


def rollup_status(summary: Any) -> str:
    # Scheduling placement does not determine a version's completion status.
    # Completion totals include all members, including reference rows.
    if summary.completion_total_story_points <= 0:
        return "In planning / no completion points"
    if summary.percent_complete >= 100:
        return "Complete"
    if summary.percent_complete <= 0:
        return "Not started"
    return "In progress"


def render_review_type_summary(plan: RunPlan) -> str:
    categories: Dict[str, Dict[str, Any]] = {}
    for item in plan.audit_items:
        bucket = categories.setdefault(
            item.category,
            {
                "count": 0,
                "severity": item.severity,
                "color": item.color,
                "reviewer_action": item.reviewer_action,
            },
        )
        bucket["count"] += 1
        bucket["severity"] = highest_severity(bucket["severity"], item.severity)
        if not bucket["color"] and item.color:
            bucket["color"] = item.color
        if not bucket["reviewer_action"] and item.reviewer_action:
            bucket["reviewer_action"] = item.reviewer_action

    rows = [
        [
            category,
            bucket["count"],
            bucket["severity"],
            color_label(bucket["color"]),
            bucket["reviewer_action"],
        ]
        for category, bucket in sorted(
            categories.items(),
            key=lambda entry: (severity_rank(entry[1]["severity"]), entry[0]),
        )
    ]
    return render_table(
        "Review Type Summary",
        ["Category", "Items", "Highest Severity", "Color", "Typical Reviewer Action"],
        rows,
    )


def highest_severity(current: str, candidate: str) -> str:
    return current if severity_rank(current) <= severity_rank(candidate) else candidate


def severity_rank(severity: str) -> int:
    return {"Error": 0, "Warning": 1, "Review": 2, "Info": 3}.get(severity, 4)


def color_label(color: str) -> str:
    return {
        "changed_cell": "Green",
        "cascade_root": "Red",
        "review_needed": "Yellow/amber",
        "dependency_review": "Light gray",
        "in_planning": "Gray/green-gray",
    }.get(color, "")


def color_key() -> str:
    return """<section>
  <h2>Color Key</h2>
  <div class="swatches">
    <div class="swatch"><span class="dot changed"></span>Green: changed cell, including autoscheduled Start/Finish</div>
    <div class="swatch"><span class="dot cascade"></span>Red: schedule driver cards in the report diagram only; Project date cells remain green</div>
    <div class="swatch"><span class="dot review"></span>Yellow/amber: unmatched or manager review needed</div>
    <div class="swatch"><span class="dot dependency"></span>Light gray: dependency review marker</div>
    <div class="swatch"><span class="dot planning"></span>Gray/green-gray: in planning</div>
  </div>
</section>"""


def render_color_examples(plan: RunPlan) -> str:
    cases = [
        (
            "changed_cell",
            "Green",
            "Changed cell",
            "A Jira value changed, Project autoscheduling shifted Start/Finish, a dependency changed, or a new epic was added.",
        ),
        (
            "cascade_root",
            "Red",
            "Schedule driver card (report diagram only)",
            "Project run only. Appears for a finish change linked to changed dates on unfinished dated successors; the Project Finish cell remains green.",
        ),
        (
            "review_needed",
            "Yellow/amber",
            "Reviewer attention",
            "The item is unmatched, excluded, or otherwise needs manager review.",
        ),
        (
            "dependency_review",
            "Light gray",
            "Dependency review",
            "A dependency needs review, Jira dates are missing, or a reference row needs explanation.",
        ),
        (
            "in_planning",
            "Gray/green-gray",
            "In planning",
            "The epic has no pointed child stories/tasks and is included as planning work.",
        ),
    ]
    rows = []
    for color_key_name, display_color, meaning, fallback in cases:
        if color_key_name == "cascade_root":
            item = next((audit for audit in plan.audit_items if audit.category == "CascadeBranchDriver"), None)
        else:
            item = next((audit for audit in plan.audit_items if audit.color == color_key_name), None)
        if item:
            jira_key = item.jira_key
            category = item.category
            field = item.field
            example = item.message
            if color_key_name == "cascade_root":
                example += " Red applies to the report diagram card only; the Project Finish cell remains green."
        elif color_key_name == "cascade_root":
            candidate = schedule_driver_candidate(plan)
            jira_key = candidate.jira_key if candidate else ""
            category = "Project run only"
            field = "Finish"
            example = (
                "Validate mode cannot identify red report diagram cards. This kind of Jira target-end change "
                "becomes a red diagram example only after Microsoft Project auto-scheduling identifies it "
                "as a changed finish linked to date movement on unfinished dated successors. The Project Finish cell remains green."
            )
        else:
            jira_key = ""
            category = "Not present in this run"
            field = ""
            example = fallback
        rows.append(
            "<tr>"
            f"<td><span class=\"dot {html_escape(color_class(color_key_name))}\"></span>{html_escape(display_color)}</td>"
            f"<td>{html_escape(meaning)}</td>"
            f"<td>{html_escape(jira_key)}</td>"
            f"<td>{html_escape(category)}</td>"
            f"<td>{html_escape(field)}</td>"
            f"<td>{html_escape(example)}</td>"
            "</tr>"
        )
    return (
        "<section><h2>Color Case Examples</h2>"
        "<table><thead><tr>"
        "<th>Color</th><th>Meaning</th><th>Example Jira Key</th><th>Category</th><th>Field</th><th>Example</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def color_class(color_key_name: str) -> str:
    return {
        "changed_cell": "changed",
        "cascade_root": "cascade",
        "review_needed": "review",
        "dependency_review": "dependency",
        "in_planning": "planning",
    }.get(color_key_name, "")


def render_planning_horizon_review(plan: RunPlan) -> str:
    items = [
        item
        for item in plan.audit_items
        if item.severity in {"Error", "Warning", "Review"} or item.category == "FutureInPlanning"
    ]
    if not items:
        return render_table("Reviewer Action Needed By Planning Horizon", [], [])

    buckets: Dict[str, List[AuditItem]] = {}
    for item in items:
        buckets.setdefault(item.planning_bucket or "Unbucketed", []).append(item)

    sections = [
        (
            planning_bucket_title(bucket),
            sorted(
                bucket_items,
                key=lambda item: (
                    severity_rank(item.severity),
                    item.planning_date or "9999-12-31",
                    item.category,
                    item.jira_key,
                ),
            ),
        )
        for bucket, bucket_items in sorted(
            buckets.items(),
            key=lambda entry: planning_bucket_sort_key(entry[0]),
        )
    ]
    return (
        "<section><h2>Reviewer Action Needed By Planning Horizon</h2>"
        "<p class=\"muted\">Immediate means the planning date is less than six months from the configured "
        "planning horizon date. Later items are grouped into six-month buckets. Future in-planning items are "
        "tracked here but are not counted as immediate task-breakdown actions.</p></section>"
        + render_sections(sections)
    )


def planning_bucket_title(bucket: str) -> str:
    if bucket == "Immediate":
        return "Immediate Review Items"
    if bucket == "Unscheduled":
        return "Unscheduled Review Items"
    if bucket == "Unbucketed":
        return "Review Items Without Horizon Buckets"
    return f"{bucket} Review Items"


def planning_bucket_sort_key(bucket: str) -> tuple[int, int, str]:
    if bucket == "Immediate":
        return (0, 0, bucket)
    if bucket == "Unscheduled":
        return (1, 0, bucket)
    if bucket == "Unbucketed":
        return (2, 0, bucket)
    try:
        start_text = bucket.split("-", 1)[0]
        return (3, int(start_text), bucket)
    except (TypeError, ValueError):
        return (4, 0, bucket)


def schedule_driver_candidate(plan: RunPlan) -> Optional[AuditItem]:
    date_changes = [
        item
        for item in plan.audit_items
        if item.category == "ChangedField" and item.field == "Jira Target End"
    ]
    preferred = [
        item
        for item in date_changes
        if "red" in item.summary.lower()
        or "cascade" in item.summary.lower()
        or "schedule driver" in item.summary.lower()
    ]
    return (preferred or date_changes or [None])[0]


def render_planned_epics(plan: RunPlan, collapsible: bool = False) -> str:
    rows = []
    for epic in sorted(plan.epics.values(), key=lambda item: (item.rollup_key, item.key)):
        rows.append(
            [
                epic.jira_key or epic.key,
                epic.key,
                epic.key_prefix,
                epic.summary,
                epic.rollup_mode,
                epic.rollup_key,
                epic.row_role,
                epic.fix_version,
                "Yes" if epic.drives_schedule else "No",
                epic.resource_group,
                epic.percent_complete,
                format_number(epic.logged_hours),
                format_number(epic.story_point_ratio),
                "Yes" if epic.in_planning else "No",
                "Yes" if epic.completed else "No",
                epic.target_start,
                epic.target_end,
                ", ".join(epic.predecessors),
                epic.dependency_review,
            ]
        )
    table = render_table(
        "Planned Epic Rows",
        [
            "Jira Key",
            "Schedule Key",
            "Project Key",
            "Summary",
            "Rollup Mode",
            "Rollup",
            "Row Role",
            "Fix Version",
            "Drives Schedule",
            "Resource Group",
            "% Complete",
            "Logged Hours",
            story_point_ratio_label(plan),
            "In Planning",
            "Done",
            "Target Start",
            "Target End",
            "Predecessors",
            "Dependency Review",
        ],
        rows,
    )
    if not collapsible:
        return table
    return render_collapsible(
        "Full Planned Epic Rows",
        table,
        f"{len(rows)} rows. Open for full row-level schedule detail.",
    )


def render_prefix_rollup_map(plan: RunPlan, config: Dict[str, Any]) -> str:
    rows = []
    configured_modes = config.get("rollup_modes", {})
    resource_groups = config.get("resource_groups", {})
    prefixes = sorted(set(resource_groups) | set(configured_modes) | set(plan.stats.get("project_keys", [])))
    for prefix in prefixes:
        rollup_mode = configured_modes.get(prefix, "")
        rows.append(
            [
                prefix,
                resource_groups.get(prefix, ""),
                rollup_mode,
                multi_fixversion_policy_for_prefix(config, prefix) if rollup_mode == "fixVersion" else "",
            ]
        )
    return render_table(
        "Project Key Rollup Mapping",
        ["Project Key", "Resource Group", "Rollup Mode", "Multi-FixVersion Policy"],
        rows,
    )


def render_sections(sections: Sequence[tuple[str, Sequence[AuditItem]]]) -> str:
    html_parts: List[str] = []
    for title, items in sections:
        rows = [
            [
                item.severity,
                item.category,
                item.jira_key,
                item.schedule_key,
                item.summary,
                item.field,
                item.old_value,
                item.new_value,
                item.planning_date,
                item.planning_bucket,
                item.message,
                item.reviewer_action,
            ]
            for item in items
        ]
        html_parts.append(
            render_table(
                title,
                [
                    "Severity",
                    "Category",
                    "Jira Key",
                    "Schedule Key",
                    "Summary",
                    "Field",
                    "Old",
                    "New",
                    "Planning Date",
                    "Planning Bucket",
                    "Message",
                    "Reviewer Action",
                ],
                rows,
            )
        )
    return "\n".join(html_parts)


def render_column_map(plan: RunPlan, collapsible: bool = False) -> str:
    rows = [[key, value or "not present"] for key, value in sorted(plan.column_map.items())]
    table = render_table("CSV Column Mapping Used", ["Logical Field", "CSV Header"], rows)
    if not collapsible:
        return table
    return render_collapsible(
        "CSV Column Mapping Used",
        table,
        f"{len(rows)} logical fields. Open to verify Jira CSV header mapping.",
    )


def render_collapsible(title: str, content: str, summary_note: str = "") -> str:
    note = f"<span class=\"summary-note\">{html_escape(summary_note)}</span>" if summary_note else ""
    return (
        "<details class=\"detail-block\">"
        f"<summary><span>{html_escape(title)}</span>{note}</summary>"
        f"<div class=\"detail-body\">{content}</div>"
        "</details>"
    )


def detail_summary(sections: Sequence[tuple[str, Sequence[AuditItem]]]) -> str:
    item_count = sum(len(items) for _title, items in sections)
    section_count = len(sections)
    return f"{item_count} items across {section_count} review sections."


def render_table(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return f"<section><h2>{html_escape(title)}</h2><p class=\"empty\">No items.</p></section>"
    header_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append("<tr>" + "".join(f"<td>{html_escape(value)}</td>" for value in row) + "</tr>")
    return (
        f"<section><h2>{html_escape(title)}</h2><div class=\"table-wrap\"><table><thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(row_html)}</tbody></table></div></section>"
    )


def by_category(items: Iterable[AuditItem], category: str) -> List[AuditItem]:
    return [item for item in items if item.category == category]


def project_key_from_jira_key(jira_key: str) -> str:
    if not jira_key:
        return "UNASSIGNED"
    return jira_key.split("-", 1)[0].upper() if "-" in jira_key else jira_key.upper()


def safe_filename(value: str) -> str:
    safe = []
    for character in value:
        if character.isalnum() or character in {"-", "_", "."}:
            safe.append(character)
        else:
            safe.append("_")
    return "".join(safe) or "UNASSIGNED"
