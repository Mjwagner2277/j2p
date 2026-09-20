"""Point Gantt summary bars at verified display dates without scheduling writes."""

from __future__ import annotations

from .models import AuditItem


def _aliases(session, column, config, fallback=()):
    values = [column, *fallback]
    try:
        values.extend(session.project_selection_aliases(column, config))
    except Exception:
        pass
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result[:4]


def configure_schedule_display(session, plan, config) -> bool:
    """Edit an existing Summary bar style, or retain one cosmetic warning.

    GanttBarStyleEdit accepts style names and date-field names. Localized field
    aliases are bounded candidates, not proof of a localized style name. Never
    guess a style row number or append a replacement on ambiguous failure.
    Project exposes no corresponding style readback API here; acceptance of
    this command does not establish the rendered appearance.
    """
    if not plan.summaries:
        return True
    fields = config.get("project_fields", {})
    names = config.get("project_field_names", {})
    starts = _aliases(session, fields.get("schedule_start", "Date3"), config,
                      (names.get("schedule_start", "Schedule Start"),))
    finishes = _aliases(session, fields.get("schedule_finish", "Date4"), config,
                        (names.get("schedule_finish", "Schedule Finish"),))
    styles = _aliases(session, "Summary", config)
    errors = []
    attempts = 0
    for style in styles:
        for start in starts:
            for finish in finishes:
                attempts += 1
                try:
                    result = session.app.GanttBarStyleEdit(
                        Item=style, Create=False, From=start, To=finish,
                    )
                    if result is True or (isinstance(result, (int, float)) and result != 0):
                        plan.stats["project_schedule_display"] = {"configured": True, "attempts": attempts}
                        return True
                    errors.append(f"style={style!r}, From={start!r}, To={finish!r}: command did not confirm success")
                except Exception as exc:
                    errors.append(f"style={style!r}, From={start!r}, To={finish!r}: {exc}")
    plan.stats["project_schedule_display"] = {"configured": False, "attempts": attempts}
    plan.audit_items.append(AuditItem(
        "Warning", "ProjectScheduleDisplayFormattingFailed", field="Schedule display",
        message=("Project could not configure the Gantt summary bar to use the verified schedule display dates. "
                 "This is a display-only failure; it does not change task scheduling. " + " | ".join(errors[:3])),
        reviewer_action=("Use Schedule Start and Schedule Finish columns. Confirm that the Gantt Summary bar "
                         "uses the configured schedule date fields in Microsoft Project."),
    ))
    return False
