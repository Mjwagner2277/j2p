"""Manager and audit report writers."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .core import (
    AuditItem,
    RunPlan,
    calculate_percent,
    format_number,
    html_escape,
    multi_fixversion_policy_for_prefix,
    summary_id,
)


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
        rows.append({key: row.get(key, "") for key in PLANNED_EPIC_COLUMNS})
    return rows


def summary_rollup_rows(plan: RunPlan) -> List[Dict[str, Any]]:
    rows = []
    for summary in sorted(plan.summaries.values(), key=lambda item: (item.rollup_mode, item.key)):
        row = asdict(summary)
        row["rollup_key"] = row.pop("key")
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
        if driving_epics:
            percent_complete = calculate_percent(completed, total)
        else:
            reference_total = round(sum(epic.total_story_points for epic in reference_epics), 2)
            reference_completed = round(sum(epic.completed_story_points for epic in reference_epics), 2)
            percent_complete = calculate_percent(reference_completed, reference_total)
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
        "| Resource Group | Resource Group | Populated from the Jira key prefix mapping in `resource_groups`. |",
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
            "Color key:",
            "",
            "- Green: changed cell",
            "- Red: first/root critical-path end-date driver; red overrides green",
            "- Yellow/amber: unmatched or manager review needed",
            "- Blue: dependency review marker",
            "- Gray/green-gray: in planning",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


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
    }}
    body {{
      font-family: Arial, Helvetica, sans-serif;
      margin: 0;
      color: var(--text);
      background: var(--bg);
      line-height: 1.4;
    }}
    header {{
      padding: 24px 32px;
      border-bottom: 1px solid var(--border);
      background: #f6f8fa;
    }}
    main {{
      padding: 24px 32px 40px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    h2 {{
      margin: 28px 0 10px;
      font-size: 20px;
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
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .metric {{
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      background: white;
    }}
    .metric strong {{
      display: block;
      font-size: 24px;
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
    <h1>j2p Manager Review Report</h1>
    <p class="muted">Generated {html_escape(plan.generated_at)}</p>
    <p>Jira CSV: {html_escape(plan.jira_csv)}</p>
    <p>Rollup mode: {html_escape(plan.rollup_mode)}</p>
    <p>Sandbox Project file: {html_escape(str(sandbox_path) if sandbox_path else "not created in validate mode")}</p>
    <p>State file: {html_escape(str(state_path) if state_path else "not written")}</p>
    <p>Per-project-key CSVs: {html_escape(str(path.parent / "by-project-key"))}</p>
  </header>
  <main>
    {summary_grid(plan)}
    {render_rollup_status(plan)}
    {render_sections([("Reviewer Action Needed", action_needed)])}
    {render_review_type_summary(plan)}
    {render_prefix_rollup_map(plan, config)}
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


def summary_grid(plan: RunPlan) -> str:
    metrics = [
        ("CSV Rows", plan.stats.get("csv_rows_read", 0)),
        ("Epics Included", plan.stats.get("epics_included", 0)),
        ("Epics Excluded", plan.stats.get("epics_excluded", 0)),
        ("Rollup Rows", plan.stats.get("summary_rows", 0)),
        ("Project Keys", len(plan.stats.get("project_keys", []))),
        ("Planned Epic Rows", plan.stats.get("planned_epic_rows", plan.stats.get("epics_included", 0))),
        ("Multi-FixVersion Epics", plan.stats.get("multi_fixversion_epics", 0)),
        ("Review Items", len([i for i in plan.audit_items if i.severity in {"Error", "Warning", "Review"}])),
    ]
    cards = "\n".join(
        f"<div class=\"metric\"><span>{html_escape(label)}</span><strong>{html_escape(value)}</strong></div>"
        for label, value in metrics
    )
    return f"<section><h2>Executive Summary</h2><div class=\"summary-grid\">{cards}</div></section>"


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
    <div class="swatch"><span class="dot cascade"></span>Red: first/root critical-path end-date driver; overrides green</div>
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
            "Critical-path root finish change",
            "Project update only. Appears after Microsoft Project auto-scheduling identifies the first/root finish-date driver.",
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
                "as the first/root finish-date driver."
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
        rollup_mode = configured_modes.get(prefix, config.get("rollup_mode", "initiative"))
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
