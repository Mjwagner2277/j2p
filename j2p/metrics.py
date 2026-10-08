"""Planning metrics used across j2p."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .models import JiraIssue


def issue_not_started(issue: JiraIssue, config: Dict[str, Any]) -> bool:
    if issue.status.strip().casefold() in {s.strip().casefold() for s in config.get("done_statuses", [])}:
        return False
    category = issue.status_category.strip().casefold()
    if category:
        return category in {"to do", "new"}
    return issue.status.strip().casefold() in {
        s.strip().casefold() for s in config.get("not_started_statuses", [])
    }


def select_story_points(
    issue: JiraIssue, child_total: float, child_count: int, config: Dict[str, Any],
) -> Tuple[float, str]:
    """Use parent estimates until the actual child breakdown takes precedence."""
    original = issue.original_story_points or 0.0
    if not child_count:
        return round(original, 2), "Original estimate (no children)"
    if issue_not_started(issue, config) and original > child_total:
        return round(original, 2), "Higher original estimate (not started)"
    return round(child_total, 2), "Child issues"


def calculate_percent(completed: float, total: float) -> int:
    if total <= 0:
        return 0
    return int(round((completed / total) * 100))


def calculate_story_point_ratio(
    completed_logged_hours: float,
    completed_story_points: float,
    hours_per_story_point: float = 8.0,
) -> float:
    """Return completed story points delivered per configured standard-hour block."""
    if completed_story_points <= 0 or completed_logged_hours <= 0 or hours_per_story_point <= 0:
        return 0.0
    return round(completed_story_points / (completed_logged_hours / hours_per_story_point), 2)
