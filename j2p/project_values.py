"""Portable preflight checks shared by planning and Microsoft Project writes."""

import math

from .models import J2PError


def value_metadata(value):
    detail = f"type={type(value).__name__}"
    if isinstance(value, str):
        detail += f", text_length={len(value)}, attempted_text={value!r}"
    return detail


def check_project_value(field, value, context):
    reason = ""
    if (field.startswith("Text") or field in {"Name", "ResourceGroup"}) and isinstance(value, str):
        if len(value) > 255:
            reason = "text exceeds Project's 255-character limit; shorten the source text or resolve dependency warnings"
    if field.startswith("Number") or field == "PercentComplete":
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            reason = "a finite numeric value is required"
        elif field == "PercentComplete" and not 0 <= value <= 100:
            reason = "percent complete must be between 0 and 100"
    if reason:
        raise J2PError(f"Project preflight failed: {context}, field={field}, {value_metadata(value)}: {reason}.")


def project_dependency_review(text: str) -> str:
    """Bound only the Project display value; the plan retains every warning."""
    if len(text) <= 255:
        return text
    suffix = " ... [Full details: reports/csv/dependency-review.csv]"
    return text[:255 - len(suffix)].rstrip() + suffix


def epic_assignments(epic, config):
    fields = config["project_fields"]
    yield "Name", epic.summary
    yield "PercentComplete", epic.percent_complete
    values = {
        "jira_key": epic.jira_key or epic.key,
        "jira_issue_id": epic.issue_id,
        "jira_issue_type": "Epic",
        "rollup_mode": epic.rollup_mode,
        "rollup_key": epic.rollup_key,
        "jira_key_prefix": epic.key_prefix,
        "dependency_review": project_dependency_review(epic.dependency_review),
        "jira_status": epic.status,
        "j2p_key": epic.key,
        "row_role": epic.row_role,
        "fix_version": epic.fix_version,
        "primary_schedule_key": epic.primary_schedule_key,
        "total_story_points": epic.total_story_points,
        "completed_story_points": epic.completed_story_points,
        "logged_hours": epic.logged_hours,
        "story_point_ratio": epic.story_point_ratio,
        "in_planning": bool(epic.in_planning),
        "dependency_review_needed": bool(epic.dependency_review),
        "drives_schedule": bool(epic.drives_schedule),
    }
    for logical, value in values.items():
        yield fields[logical], value


def epic_context(epic):
    return f"epic={epic.key}, CSV row={epic.source_row}, CSV file={epic.source_file}"


def validate_project_plan(plan, config):
    for epic in plan.epics.values():
        for field, value in epic_assignments(epic, config):
            check_project_value(field, value, epic_context(epic))
        check_project_value("ResourceGroup", epic.resource_group, epic_context(epic))
    fields = config["project_fields"]
    for summary in plan.summaries.values():
        context = f"rollup={summary.key}"
        check_project_value("Name", summary.name, context)
        check_project_value(fields["rollup_key"], summary.key, context)
        check_project_value("PercentComplete", summary.percent_complete, context)
        for logical in ("total_story_points", "completed_story_points", "logged_hours", "story_point_ratio"):
            check_project_value(fields[logical], getattr(summary, logical), context)
