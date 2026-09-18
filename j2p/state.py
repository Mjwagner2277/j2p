"""State file serialization for report-only comparison runs."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .models import AuditItem, ProjectTaskSnapshot, RunPlan, J2PError


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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1 or not isinstance(data.get("epics"), dict):
            raise ValueError("expected version 1 state with an epics object")
    except (OSError, ValueError) as exc:
        raise J2PError(f"Could not read state {path}: {exc}") from exc
    snapshots: Dict[str, ProjectTaskSnapshot] = {}
    for key, epic in data.get("epics", {}).items():
        validate_state_epic(path, key, epic)
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


def validate_state_epic(path: Path, key: str, epic: Any) -> None:
    def invalid(detail):
        raise J2PError(f"Invalid state {path}, epic {key}: {detail}.")
    if not isinstance(key, str) or not key or not isinstance(epic, dict):
        invalid("expected a nonempty key and an epic object")
    for field in ("total_story_points", "completed_story_points", "logged_hours", "story_point_ratio", "percent_complete"):
        value = epic.get(field, 0)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            invalid(f"{field} must be a nonnegative finite number")
    if not 0 <= epic.get("percent_complete", 0) <= 100:
        invalid("percent_complete must be between 0 and 100")
    for field in ("predecessors", "successors"):
        values = epic.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            invalid(f"{field} must be a list of strings")
    for field in ("jira_key", "summary", "issue_id", "rollup_mode", "rollup_key", "resource_group", "key_prefix", "status",
                  "target_start", "target_end", "row_role", "fix_version", "primary_schedule_key"):
        if field in epic and not isinstance(epic[field], str):
            invalid(f"{field} must be a string")
    if "drives_schedule" in epic and type(epic["drives_schedule"]) is not bool:
        invalid("drives_schedule must be a boolean")


def write_json(path: Path, data: Dict[str, Any]) -> None:
    write_bytes_atomic(path, (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
