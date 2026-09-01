"""Manager and audit report writers."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .formatting import format_number, html_escape
from .metrics import calculate_percent, calculate_story_point_ratio
from .models import AuditItem, RunPlan
from .rollups import multi_fixversion_policy_for_prefix, summary_id


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
    "logged_hours",
    "completed_logged_hours",
    "story_point_ratio",
    "percent_complete",
]


def write_reports(
    plan: RunPlan,
    run_dir: Path,
    config: Dict[str, Any],
    sandbox_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
) -> Dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "manager_report": run_dir / "Manager-Review-Report.html",
        "audit_detail": run_dir / "audit-detail.csv",
        "planned_epics": run_dir / "planned-epics.csv",
        "summary_rollups": run_dir / "summary-rollups.csv",
        "dependency_review": run_dir / "dependency-review.csv",
        "field_mapping": run_dir / "FIELD_MAPPING.md",
    }

    write_audit_csv(paths["audit_detail"], plan.audit_items)
    write_planned_epics(paths["planned_epics"], plan)
    write_summary_rollups(paths["summary_rollups"], plan)
    write_dependency_review(paths["dependency_review"], plan.audit_items)
    write_field_mapping(paths["field_mapping"], config)
    write_per_project_key_csvs(run_dir / "by-project-key", plan)
    write_manager_html(paths["manager_report"], plan, config, sandbox_path, state_path)
    paths["by_project_key"] = run_dir / "by-project-key"
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
    buckets: Dict[str, List[Any]] = {}
    for epic in plan.epics.values():
        if epic.key_prefix != project_key:
            continue
        buckets.setdefault(summary_id(epic.rollup_mode, epic.rollup_key), []).append(epic)

    rows = []
    for _bucket_id, epics in sorted(buckets.items()):
        driving_epics = [epic for epic in epics if epic.drives_schedule]
        reference_epics = [epic for epic in epics if not epic.drives_schedule]
        total = round(sum(epic.total_story_points for epic in driving_epics), 2)
        completed = round(sum(epic.completed_story_points for epic in driving_epics), 2)
        logged_hours = round(sum(epic.logged_hours for epic in driving_epics), 2)
        completed_logged_hours = round(sum(epic.completed_logged_hours for epic in driving_epics), 2)
        ratio_completed = completed
        if driving_epics:
            percent_complete = calculate_percent(completed, total)
        else:
            reference_total = round(sum(epic.total_story_points for epic in reference_epics), 2)
            reference_completed = round(sum(epic.completed_story_points for epic in reference_epics), 2)
            logged_hours = round(sum(epic.logged_hours for epic in reference_epics), 2)
            completed_logged_hours = round(sum(epic.completed_logged_hours for epic in reference_epics), 2)
            percent_complete = calculate_percent(reference_completed, reference_total)
            ratio_completed = reference_completed
        story_point_ratio = calculate_story_point_ratio(
            completed_logged_hours,
            ratio_completed,
            float(plan.stats.get("hours_per_story_point", 8.0)),
        )
        first = epics[0]
        rows.append(
            {
                "rollup_key": first.rollup_key,
                "project_key": project_key,
                "name": first.rollup_name,
                "rollup_mode": first.rollup_mode,
                "child_epic_count": len(epics),
                "driving_epic_count": len(driving_epics),
                "reference_epic_count": len(reference_epics),
                "total_story_points": total,
                "completed_story_points": completed,
                "logged_hours": logged_hours,
                "completed_logged_hours": completed_logged_hours,
                "story_point_ratio": story_point_ratio,
                "percent_complete": percent_complete,
            }
        )
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
        "| Resource Group | Resource Group | Populated by assigning a Project resource whose Group value comes from the Jira key prefix mapping in `resource_groups`. |",
        "",
        "Custom task fields used by j2p:",
        "",
        "| j2p Value | Project Field | Project Column Name |",
        "| --- | --- | --- |",
    ]
    project_fields = config.get("project_fields", {})
    project_field_names = config.get("project_field_names", {})
    for key in sorted(project_fields):
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
            "- Green: changed cell",
            "- Red: cascade branch driver finish date; red overrides green",
            "- Yellow/amber: unmatched or manager review needed",
            "- Blue: dependency review marker",
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
) -> None:
    action_needed = [
        item for item in plan.audit_items if item.severity in {"Error", "Warning", "Review"}
    ]
    detail_sections = [
        ("Changed Names", by_category(plan.audit_items, "ChangedName")),
        ("Added Epics", by_category(plan.audit_items, "AddedEpic")),
        (
            "Multi-FixVersion Epics",
            [item for item in plan.audit_items if item.category.startswith("MultiFixVersion")],
        ),
        ("Parent Or Rollup Moves", by_category(plan.audit_items, "RollupMove")),
        ("Completed Since Last Update", by_category(plan.audit_items, "CompletedSinceLastUpdate")),
        ("In Planning", by_category(plan.audit_items, "InPlanning")),
        (
            "Dependency Review",
            [
                item
                for item in plan.audit_items
                if "Dependency" in item.category or item.field == "Dependency Review"
            ],
        ),
        (
            "Date Review",
            [
                item
                for item in plan.audit_items
                if item.field in {"Jira Target Start", "Jira Target End", "Start", "Finish"}
                or "Date" in item.category
            ],
        ),
        ("Unmatched Project Tasks", by_category(plan.audit_items, "UnmatchedProjectTask")),
        (
            "Excluded Items",
            [
                item
                for item in plan.audit_items
                if item.category.startswith("Excluded") or item.category == "CsvRowMissingJiraKey"
            ],
        ),
    ]

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>j2p Manager Review Report</title>
  <style>
    :root {{
      --changed: {html_escape(config["colors"]["changed_cell"])};
      --cascade: {html_escape(config["colors"]["cascade_root"])};
      --review: {html_escape(config["colors"]["review_needed"])};
      --dependency: {html_escape(config["colors"]["dependency_review"])};
      --planning: {html_escape(config["colors"]["in_planning"])};
      --border: #d0d7de;
      --text: #1f2328;
      --muted: #59636e;
      --bg: #ffffff;
      --section: #f6f8fa;
      --accent: #2f6f5e;
    }}
    body {{
      font-family: Arial, Helvetica, sans-serif;
      margin: 0;
      color: var(--text);
      background: var(--bg);
      line-height: 1.4;
    }}
    header {{
      padding: 22px 32px;
      border-bottom: 1px solid var(--border);
      background: #ffffff;
    }}
    main {{
      padding: 24px 32px 40px;
      max-width: 1440px;
      margin: 0 auto;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 26px;
      font-weight: 700;
    }}
    h2 {{
      margin: 28px 0 10px;
      font-size: 18px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
    }}
    h3 {{
      margin: 18px 0 8px;
      font-size: 16px;
    }}
    p {{
      margin: 6px 0;
    }}
    .muted {{
      color: var(--muted);
    }}
    .headline {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-start;
      max-width: 1440px;
      margin: 0 auto;
    }}
    .headline-meta {{
      text-align: right;
      color: var(--muted);
      font-size: 13px;
    }}
    .briefing-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 10px;
      margin: 12px 0 18px;
    }}
    .briefing-item {{
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      padding: 10px 12px;
      background: #ffffff;
    }}
    .briefing-item strong {{
      display: block;
      font-size: 22px;
      margin-top: 2px;
    }}
    .briefing-item span {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    .cascade-review {{
      border: 1px solid var(--border);
      border-left: 4px solid var(--cascade);
      padding: 12px 14px;
      background: #ffffff;
      margin-bottom: 18px;
    }}
    .cascade-help {{
      margin: 0 0 12px;
      color: var(--muted);
    }}
    .cascade-flow {{
      display: grid;
      gap: 12px;
      margin: 12px 0;
    }}
    .cascade-branch {{
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      background: #ffffff;
    }}
    details.cascade-branch {{
      padding: 0;
    }}
    details.cascade-branch > summary {{
      cursor: pointer;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
      padding: 10px;
      font-weight: 600;
    }}
    details.cascade-branch > summary::-webkit-details-marker {{
      display: none;
    }}
    details.cascade-branch > summary::before {{
      content: "+";
      color: var(--cascade);
      margin-right: 2px;
    }}
    details.cascade-branch[open] > summary::before {{
      content: "-";
    }}
    .cascade-branch-body {{
      border-top: 1px solid var(--border);
      padding: 10px;
    }}
    .cascade-branch-count {{
      color: var(--muted);
      font-size: 12px;
      font-weight: normal;
    }}
    .cascade-node {{
      border: 1px solid var(--border);
      border-left: 5px solid var(--changed);
      border-radius: 6px;
      padding: 9px 10px;
      background: #ffffff;
      min-width: 220px;
    }}
    .cascade-node.driver {{
      border-left-color: var(--cascade);
      background: #fffafa;
    }}
    .cascade-node.changed {{
      border-left-color: var(--changed);
      background: #fbfffb;
    }}
    .cascade-node-title {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: baseline;
      justify-content: space-between;
      font-weight: 600;
    }}
    .cascade-node-title span {{
      color: var(--muted);
      font-weight: normal;
      font-size: 12px;
    }}
    .cascade-node-summary {{
      margin-top: 4px;
      font-size: 13px;
    }}
    .cascade-node-dates {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 5px;
    }}
    .cascade-children {{
      border-left: 2px solid var(--border);
      margin: 8px 0 0 16px;
      padding-left: 14px;
      display: grid;
      gap: 8px;
    }}
    .table-wrap {{
      width: 100%;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 10px 0 18px;
      font-size: 13px;
    }}
    th, td {{
      border: 1px solid var(--border);
      padding: 7px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: var(--section);
      font-weight: 600;
    }}
    .priority-table tbody tr:first-child td {{
      border-top: 2px solid var(--accent);
    }}
    .empty {{
      color: var(--muted);
      font-style: italic;
      margin: 8px 0 18px;
    }}
    .swatches {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 8px;
      margin-top: 8px;
    }}
    .swatch {{
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
    }}
    .dot {{
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 1px solid #8c959f;
      margin-right: 6px;
      vertical-align: -2px;
    }}
    .changed {{ background: var(--changed); }}
    .cascade {{ background: var(--cascade); }}
    .review {{ background: var(--review); }}
    .dependency {{ background: var(--dependency); }}
    .planning {{ background: var(--planning); }}
    details.detail-block {{
      border: 1px solid var(--border);
      border-radius: 6px;
      margin: 20px 0;
      background: white;
    }}
    details.detail-block > summary {{
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 14px;
      background: var(--section);
      font-weight: bold;
    }}
    details.detail-block[open] > summary {{
      border-bottom: 1px solid var(--border);
    }}
    .detail-body {{
      padding: 0 14px 14px;
    }}
    .summary-note {{
      font-size: 12px;
      font-weight: normal;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <header>
    <div class="headline">
      <div>
        <h1>Schedule Review Report</h1>
        <p class="muted">Jira-to-Project sandbox review packet</p>
      </div>
      <div class="headline-meta">
        <p>Generated {html_escape(plan.generated_at)}</p>
        <p>Rollup mode: {html_escape(plan.rollup_mode)}</p>
      </div>
    </div>
  </header>
  <main>
    {decision_briefing(plan)}
    {render_story_point_ratio_breakdown(plan)}
    {render_rollup_status(plan)}
    {render_schedule_cascade_review(plan, project_update_run=sandbox_path is not None)}
    {render_sections([("Reviewer Action Needed", action_needed)])}
    {render_review_type_summary(plan)}
    {render_prefix_rollup_map(plan, config)}
    {render_report_context(plan, sandbox_path, state_path, path.parent / "by-project-key")}
    {color_key()}
    {render_color_examples(plan)}
    {render_collapsible("Detailed Review Sections", render_sections(detail_sections), detail_summary(detail_sections))}
    {render_planned_epics(plan, collapsible=True)}
    {render_column_map(plan, collapsible=True)}
  </main>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def decision_briefing(plan: RunPlan) -> str:
    action_items = [item for item in plan.audit_items if item.severity in {"Error", "Warning", "Review"}]
    dependency_items = [
        item
        for item in plan.audit_items
        if "Dependency" in item.category or item.field in {"Predecessors", "Successors", "Dependency Review"}
    ]
    completed_items = by_category(plan.audit_items, "CompletedSinceLastUpdate")
    in_progress_rollups = sum(1 for summary in plan.summaries.values() if 0 < summary.percent_complete < 100)
    completed_rollups = sum(1 for summary in plan.summaries.values() if summary.percent_complete >= 100)
    ratio_summary = project_wide_story_point_ratio_summary(plan)
    metrics = [
        ("Needs Review", len(action_items), "Warnings and review decisions"),
        ("Rollups In Progress", in_progress_rollups, f"{completed_rollups} complete"),
        ("Dependency Items", len(dependency_items), "Changed, missing, skipped, or circular"),
        ("Completed Epics", len(completed_items), "Completed since comparison baseline"),
        ("Logged Hours", format_number(plan.stats.get("logged_hours", 0)), "Rolled up from child work"),
        (
            story_point_ratio_label(plan),
            format_number(ratio_summary["story_point_ratio"]),
            pluralize(ratio_summary["epic_count"], "in-progress row", "in-progress rows"),
        ),
    ]
    return f"<section><h2>Decision Briefing</h2><div class=\"briefing-grid\">{render_metric_cards(metrics)}</div></section>"


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


def render_schedule_cascade_review(plan: RunPlan, project_update_run: bool = False) -> str:
    cascade_items = schedule_cascade_change_items(plan)
    if not cascade_items:
        message = (
            "No Project auto-schedule finish-date changes were detected in this update run."
            if project_update_run
            else (
                "No Project auto-schedule finish-date changes were evaluated in this report. "
                "This section is populated during create/update runs after Microsoft Project recalculates the sandbox."
            )
        )
        return f"<section><h2>Schedule Cascade Review</h2><p class=\"empty\">{html_escape(message)}</p></section>"

    changed_keys = set(cascade_items)
    driver_keys = {
        key for key, item in cascade_items.items() if item.category == "CascadeBranchDriver"
    }
    leaf_keys = changed_keys - driver_keys
    roots = cascade_root_keys(plan, changed_keys)
    downstream_counts = cascade_downstream_counts(plan, changed_keys)
    branch_roots = [
        key for key in roots if key in driver_keys or changed_successors(plan, key, changed_keys)
    ]
    branch_roots = sorted(
        branch_roots,
        key=lambda key: (-downstream_counts.get(key, 0), key),
    )
    metrics = [
        ("Finish Changes", len(changed_keys), "Changed after Project recalculated the sandbox"),
        ("Red Branch Drivers", len(driver_keys), "Changed rows with changed downstream successors"),
        ("Green Finish Changes", len(leaf_keys), "Changed leaves or independent rows"),
    ]
    branch_html = "".join(
        render_cascade_branch(plan, cascade_items, root_key, downstream_counts)
        for root_key in branch_roots
    )
    if not branch_html:
        branch_html = "<p class=\"empty\">No cascade branches were detected. Finish changes appear independent or leaf-only.</p>"
    detail_table = render_collapsible(
        "Schedule Cascade Detail",
        render_schedule_cascade_table(plan, cascade_items),
        f"{len(changed_keys)} finish-date change(s). Open for old/new dates and changed downstream successors.",
    )
    return (
        "<section><h2>Schedule Cascade Review</h2>"
        "<div class=\"cascade-review\">"
        f"<div class=\"briefing-grid\">{render_metric_cards(metrics)}</div>"
        "<p class=\"cascade-help\">"
        "Red cards are changed finish dates that also have changed downstream successors. "
        "Green cards are changed finish dates with no changed downstream successor. "
        "Nested branches follow the Jira dependency links written to Project as predecessor relationships."
        "</p>"
        f"<div class=\"cascade-flow\">{branch_html}</div>"
        "</div></section>"
        f"{detail_table}"
    )


def schedule_cascade_change_items(plan: RunPlan) -> Dict[str, AuditItem]:
    items: Dict[str, AuditItem] = {}
    for item in plan.audit_items:
        if item.category not in {"CascadeBranchDriver", "CascadingDateChange"}:
            continue
        key = (item.schedule_key or item.jira_key).upper()
        if key:
            items[key] = item
    return items


def cascade_root_keys(plan: RunPlan, changed_keys: set[str]) -> List[str]:
    changed_with_changed_predecessor: set[str] = set()
    for key in changed_keys:
        epic = plan.epics.get(key)
        if not epic:
            continue
        for successor_key in epic.successors:
            if successor_key in changed_keys:
                changed_with_changed_predecessor.add(successor_key)
    return sorted(changed_keys - changed_with_changed_predecessor)


def changed_successors(plan: RunPlan, key: str, changed_keys: set[str]) -> List[str]:
    epic = plan.epics.get(key)
    if not epic:
        return []
    return sorted(successor_key for successor_key in epic.successors if successor_key in changed_keys)


def cascade_downstream_counts(plan: RunPlan, changed_keys: set[str]) -> Dict[str, int]:
    return {key: len(cascade_downstream_keys(plan, key, changed_keys)) for key in changed_keys}


def cascade_downstream_keys(plan: RunPlan, key: str, changed_keys: set[str]) -> set[str]:
    downstream: set[str] = set()
    stack = list(changed_successors(plan, key, changed_keys))
    while stack:
        successor_key = stack.pop()
        if successor_key == key or successor_key in downstream:
            continue
        downstream.add(successor_key)
        stack.extend(changed_successors(plan, successor_key, changed_keys))
    return downstream


def sorted_changed_successors(
    plan: RunPlan,
    key: str,
    changed_keys: set[str],
    downstream_counts: Dict[str, int],
) -> List[str]:
    return sorted(
        changed_successors(plan, key, changed_keys),
        key=lambda successor_key: (-downstream_counts.get(successor_key, 0), successor_key),
    )


def render_cascade_branch(
    plan: RunPlan,
    cascade_items: Dict[str, AuditItem],
    root_key: str,
    downstream_counts: Dict[str, int],
) -> str:
    downstream_count = downstream_counts.get(root_key, 0)
    branch_body = render_cascade_node(plan, cascade_items, root_key, set(), downstream_counts)
    if downstream_count <= 5:
        return f"<div class=\"cascade-branch\">{branch_body}</div>"

    item = cascade_items[root_key]
    epic = plan.epics.get(root_key)
    jira_key = item.jira_key or (epic.jira_key if epic else "") or root_key
    summary = item.summary or (epic.summary if epic else "")
    return (
        "<details class=\"cascade-branch\">"
        "<summary>"
        f"<span>{html_escape(jira_key)}: {html_escape(summary)}</span>"
        f"<span class=\"cascade-branch-count\">"
        f"{html_escape(pluralize(downstream_count, 'downstream affected issue'))}"
        "</span>"
        "</summary>"
        f"<div class=\"cascade-branch-body\">{branch_body}</div>"
        "</details>"
    )


def render_cascade_node(
    plan: RunPlan,
    cascade_items: Dict[str, AuditItem],
    key: str,
    path: set[str],
    downstream_counts: Dict[str, int],
) -> str:
    item = cascade_items[key]
    is_driver = item.category == "CascadeBranchDriver"
    epic = plan.epics.get(key)
    jira_key = item.jira_key or (epic.jira_key if epic else "") or key
    schedule_key = item.schedule_key or key
    summary = item.summary or (epic.summary if epic else "")
    changed_keys = set(cascade_items)
    successor_keys = [] if key in path else sorted_changed_successors(plan, key, changed_keys, downstream_counts)
    label = "Driver" if is_driver else "Changed"
    node_html = (
        f"<div class=\"cascade-node {'driver' if is_driver else 'changed'}\">"
        "<div class=\"cascade-node-title\">"
        f"{html_escape(jira_key)} <span>{html_escape(label)}</span>"
        "</div>"
        f"<div class=\"cascade-node-summary\">{html_escape(summary)}</div>"
        f"<div class=\"cascade-node-dates\">Finish: {html_escape(item.old_value or 'blank')} -> {html_escape(item.new_value or 'blank')}</div>"
    )
    if schedule_key != jira_key:
        node_html += f"<div class=\"cascade-node-dates\">Schedule row: {html_escape(schedule_key)}</div>"
    node_html += "</div>"
    if successor_keys:
        child_path = set(path)
        child_path.add(key)
        children = "".join(
            render_cascade_node(plan, cascade_items, successor_key, child_path, downstream_counts)
            for successor_key in successor_keys
        )
        node_html += f"<div class=\"cascade-children\">{children}</div>"
    return node_html


def render_schedule_cascade_table(plan: RunPlan, cascade_items: Dict[str, AuditItem]) -> str:
    changed_keys = set(cascade_items)
    rows = []
    for key, item in sorted(
        cascade_items.items(),
        key=lambda entry: (entry[1].category != "CascadeBranchDriver", entry[1].new_value, entry[0]),
    ):
        epic = plan.epics.get(key)
        successor_labels = [
            cascade_items[successor_key].jira_key or successor_key
            for successor_key in changed_successors(plan, key, changed_keys)
        ]
        rows.append(
            [
                "Red" if item.category == "CascadeBranchDriver" else "Green",
                item.jira_key or (epic.jira_key if epic else "") or key,
                item.schedule_key or key,
                item.summary or (epic.summary if epic else ""),
                item.old_value,
                item.new_value,
                ", ".join(successor_labels),
                item.reviewer_action,
            ]
        )
    return render_table(
        "Schedule Cascade Detail",
        [
            "Color",
            "Jira Key",
            "Schedule Key",
            "Summary",
            "Old Finish",
            "New Finish",
            "Changed Downstream Successors",
            "Reviewer Action",
        ],
        rows,
    )


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
) -> str:
    rows = [
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
    ]
    return render_collapsible(
        "Report Context",
        render_table("Run Inputs And Counts", ["Item", "Value"], rows),
        "Open for file paths, CSV row counts, and raw processing totals.",
    )


def render_rollup_status(plan: RunPlan) -> str:
    rows = []
    for summary in sorted(plan.summaries.values(), key=lambda item: (item.rollup_mode, item.key)):
        rows.append(
            [
                summary.name,
                summary.key,
                summary.project_key,
                summary.rollup_mode,
                rollup_status(summary),
                f"{summary.percent_complete}%",
                f"{format_number(summary.completed_story_points)} / {format_number(summary.total_story_points)}",
                format_number(summary.logged_hours),
                format_number(summary.story_point_ratio),
                summary.driving_epic_count,
                summary.reference_epic_count,
                summary.child_epic_count,
            ]
        )
    return render_table(
        "Rollup Status",
        [
            "Rollup",
            "Rollup Key",
            "Project Key",
            "Mode",
            "Status",
            "% Complete",
            "Completed / Total Points",
            "Logged Hours",
            story_point_ratio_label(plan),
            "Driving Rows",
            "Reference Rows",
            "Total Rows",
        ],
        rows,
    )


def rollup_status(summary: Any) -> str:
    if summary.driving_epic_count == 0 and summary.reference_epic_count > 0:
        return "Reference only"
    if summary.total_story_points <= 0:
        return "In planning / no counted points"
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
        "dependency_review": "Blue",
        "in_planning": "Gray/green-gray",
    }.get(color, "")


def color_key() -> str:
    return """<section>
  <h2>Color Key</h2>
  <div class="swatches">
    <div class="swatch"><span class="dot changed"></span>Green: changed cell</div>
    <div class="swatch"><span class="dot cascade"></span>Red: cascade branch driver finish date; overrides green</div>
    <div class="swatch"><span class="dot review"></span>Yellow/amber: unmatched or manager review needed</div>
    <div class="swatch"><span class="dot dependency"></span>Blue: dependency review marker</div>
    <div class="swatch"><span class="dot planning"></span>Gray/green-gray: in planning</div>
  </div>
</section>"""


def render_color_examples(plan: RunPlan) -> str:
    cases = [
        (
            "changed_cell",
            "Green",
            "Changed cell",
            "A Jira value changed, a dependency changed, or a new epic was added.",
        ),
        (
            "cascade_root",
            "Red",
            "Cascade branch driver finish change",
            "Project update only. Appears when a changed Project finish also has changed downstream successors.",
        ),
        (
            "review_needed",
            "Yellow/amber",
            "Reviewer attention",
            "The item is unmatched, excluded, or otherwise needs manager review.",
        ),
        (
            "dependency_review",
            "Blue",
            "Dependency review",
            "A dependency was changed, skipped, circular, self-referencing, or points outside the included epic set.",
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
        item = next((audit for audit in plan.audit_items if audit.color == color_key_name), None)
        if item:
            jira_key = item.jira_key
            category = item.category
            field = item.field
            example = item.message
        elif color_key_name == "cascade_root":
            candidate = schedule_driver_candidate(plan)
            jira_key = candidate.jira_key if candidate else ""
            category = "Project update only"
            field = "Finish"
            example = (
                "Validate mode does not choose the red cell. This kind of Jira target-end change "
                "becomes a red example only after Microsoft Project auto-scheduling identifies it "
                "as a changed finish date with changed downstream successors."
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
