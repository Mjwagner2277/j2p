"""Shared data models for j2p planning and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


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
    logged_hours: float
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
    logged_hours: float
    completed_logged_hours: float
    story_point_ratio: float
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
    logged_hours: float
    completed_logged_hours: float
    story_point_ratio: float
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
    logged_hours: float = 0.0
    story_point_ratio: float = 0.0
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
