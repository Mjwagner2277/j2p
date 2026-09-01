"""State file serialization for report-only comparison runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .models import AuditItem, ProjectTaskSnapshot, RunPlan


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
            logged_hours=float(epic.get("logged_hours") or 0),
            story_point_ratio=float(epic.get("story_point_ratio") or 0),
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
