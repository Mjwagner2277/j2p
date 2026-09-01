"""Planning metrics used across j2p."""

from __future__ import annotations


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
