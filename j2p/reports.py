"""Manager and audit report writers."""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .core import AuditItem, RunPlan, html_escape


AUDIT_COLUMNS = [
    "severity",
    "category",
    "jira_key",
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
    write_manager_html(paths["manager_report"], plan, config, sandbox_path, state_path)
    return paths


def write_audit_csv(path: Path, audit_items: Sequence[AuditItem]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_COLUMNS)
        writer.writeheader()
        for item in audit_items:
            writer.writerow(asdict(item))


def write_planned_epics(path: Path, plan: RunPlan) -> None:
    columns = [
        "jira_key",
        "summary",
        "status",
        "rollup_mode",
        "rollup_key",
        "rollup_name",
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
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for epic in sorted(plan.epics.values(), key=lambda item: (item.rollup_key, item.key)):
            row = asdict(epic)
            row["jira_key"] = row.pop("key")
            row["predecessors"] = ",".join(epic.predecessors)
            row["successors"] = ",".join(epic.successors)
            writer.writerow({key: row.get(key, "") for key in columns})


def write_summary_rollups(path: Path, plan: RunPlan) -> None:
    columns = [
        "rollup_key",
        "name",
        "rollup_mode",
        "child_epic_count",
        "total_story_points",
        "completed_story_points",
        "percent_complete",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for summary in sorted(plan.summaries.values(), key=lambda item: item.key):
            row = asdict(summary)
            row["rollup_key"] = row.pop("key")
            writer.writerow({key: row.get(key, "") for key in columns})


def write_dependency_review(path: Path, audit_items: Sequence[AuditItem]) -> None:
    dependency_items = [
        item
        for item in audit_items
        if "Dependency" in item.category or item.field in {"Predecessors", "Successors", "Dependency Review"}
    ]
    write_audit_csv(path, dependency_items)


def write_field_mapping(path: Path, config: Dict[str, Any]) -> None:
    lines = [
        "# j2p Microsoft Project Field Mapping",
        "",
        "These task custom fields are used by j2p when it creates or updates a sandbox Project file.",
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
    sections = [
        ("Reviewer Action Needed", action_needed),
        ("Changed Names", by_category(plan.audit_items, "ChangedName")),
        ("Added Epics", by_category(plan.audit_items, "AddedEpic")),
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
  </header>
  <main>
    {summary_grid(plan)}
    {color_key()}
    {render_planned_epics(plan)}
    {render_sections(sections)}
    {render_column_map(plan)}
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
        ("Review Items", len([i for i in plan.audit_items if i.severity in {"Error", "Warning", "Review"}])),
    ]
    cards = "\n".join(
        f"<div class=\"metric\"><span>{html_escape(label)}</span><strong>{html_escape(value)}</strong></div>"
        for label, value in metrics
    )
    return f"<section><h2>Executive Summary</h2><div class=\"summary-grid\">{cards}</div></section>"


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


def render_planned_epics(plan: RunPlan) -> str:
    rows = []
    for epic in sorted(plan.epics.values(), key=lambda item: (item.rollup_key, item.key)):
        rows.append(
            [
                epic.key,
                epic.summary,
                epic.rollup_key,
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
    return render_table(
        "Planned Epic Rows",
        [
            "Jira Key",
            "Summary",
            "Rollup",
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


def render_sections(sections: Sequence[tuple[str, Sequence[AuditItem]]]) -> str:
    html_parts: List[str] = []
    for title, items in sections:
        rows = [
            [
                item.severity,
                item.category,
                item.jira_key,
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


def render_column_map(plan: RunPlan) -> str:
    rows = [[key, value or "not present"] for key, value in sorted(plan.column_map.items())]
    return render_table("CSV Column Mapping Used", ["Logical Field", "CSV Header"], rows)


def render_table(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return f"<section><h2>{html_escape(title)}</h2><p class=\"empty\">No items.</p></section>"
    header_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append("<tr>" + "".join(f"<td>{html_escape(value)}</td>" for value in row) + "</tr>")
    return (
        f"<section><h2>{html_escape(title)}</h2><table><thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(row_html)}</tbody></table></section>"
    )


def by_category(items: Iterable[AuditItem], category: str) -> List[AuditItem]:
    return [item for item in items if item.category == category]
