"""Synchronize active membership copies; let native summaries roll up children."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Tuple

from .rollups import summary_id


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


def reference_rollup_ids(plan) -> set[str]:
    return {
        summary_id(epic.rollup_mode, epic.rollup_key)
        for epic in plan.epics.values()
        if epic.rollup_mode == "fixVersion" and not epic.drives_schedule
    }


def _required_primary_keys(plan) -> set[str]:
    return {
        _key(epic.key if epic.drives_schedule else epic.primary_schedule_key)
        for epic in plan.epics.values()
    }


def _stamp(value: Any) -> Tuple[int, int, int, int, int]:
    """Compare native dates at minute precision without replacing their times."""
    parsed = value
    if isinstance(parsed, str):
        text = parsed.strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for pattern in (
                "%m/%d/%Y %I:%M %p", "%m/%d/%y %I:%M %p",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M",
                "%m/%d/%Y", "%m/%d/%y",
                "%B %d, %Y %I:%M %p", "%B %d, %Y %I:%M%p",
            ):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("a native Project date is required") from None
    if not isinstance(parsed, (datetime, date)):
        raise ValueError("a native Project date is required")
    return (parsed.year, parsed.month, parsed.day,
            parsed.hour if isinstance(parsed, datetime) else 0,
            parsed.minute if isinstance(parsed, datetime) else 0)


def _read(task, field: str, context: str):
    from .project import ProjectAutomationError
    try:
        return getattr(task, field)
    except Exception as exc:
        raise ProjectAutomationError(
            f"Cannot read reference schedule: {context}, field={field}: {exc}"
        ) from exc


def _window(values, context: str):
    from .project import ProjectAutomationError
    try:
        start, finish = values
        start_stamp, finish_stamp = _stamp(start), _stamp(finish)
    except (TypeError, ValueError) as exc:
        raise ProjectAutomationError(
            f"Cannot synchronize reference dates: {context} has missing or invalid Project Start/Finish dates."
        ) from exc
    if start_stamp > finish_stamp:
        raise ProjectAutomationError(
            f"Cannot synchronize reference dates: {context} has Project Start after Finish."
        )
    return (start, finish)


def _targets(plan, task_by_key, summary_tasks, raw_dates):
    """Resolve every source and destination before changing any Project values."""
    from .project import ProjectAutomationError
    epics = {_key(key): epic for key, epic in plan.epics.items()}
    references = {}
    members: Dict[str, list] = {key: [] for key in plan.summaries}
    source_windows = {}
    memberships = set()

    def primary_window(epic):
        primary = _key(epic.key if epic.drives_schedule else epic.primary_schedule_key)
        source = epics.get(primary)
        if source is None or not source.drives_schedule or primary not in task_by_key:
            raise ProjectAutomationError(
                f"Cannot synchronize reference dates: epic={epic.key}, missing driving primary={primary or '(blank)'}."
            )
        if _key(source.jira_key or source.key) != _key(epic.jira_key or epic.key):
            raise ProjectAutomationError(f"Copy identity mismatch: epic={epic.key}, primary={primary} refers to a different Jira issue.")
        if primary not in source_windows:
            if primary not in raw_dates:
                raise ProjectAutomationError(
                    f"Cannot synchronize reference dates: primary={primary} has no final scheduled date readback."
                )
            source_windows[primary] = _window(raw_dates[primary], f"primary={primary}")
        return source_windows[primary]

    for epic in plan.epics.values():
        rollup = summary_id(epic.rollup_mode, epic.rollup_key)
        membership = (epic.rollup_mode, _key(epic.rollup_key), _key(epic.jira_key or epic.key))
        if membership in memberships:
            raise ProjectAutomationError(f"Duplicate Jira membership in planned rollup={rollup}: issue={membership[2]}.")
        memberships.add(membership)
        window = primary_window(epic)
        key = _key(epic.key)
        if key not in task_by_key:
            raise ProjectAutomationError(f"Cannot synchronize reference dates: missing epic={epic.key}.")
        if not epic.drives_schedule:
            references[key] = window
        if rollup not in members:
            raise ProjectAutomationError(f"Cannot synchronize reference dates: missing planned rollup={rollup}.")
        members[rollup].append(window)

    rollups = {}
    for rollup, windows in members.items():
        if not windows:
            continue
        summary = plan.summaries.get(rollup)
        if summary is None:
            raise ProjectAutomationError(f"Cannot synchronize reference dates: missing planned rollup={rollup}.")
        identity = (summary.rollup_mode, _key(summary.key))
        if identity not in summary_tasks:
            raise ProjectAutomationError(f"Cannot synchronize reference dates: missing Project rollup={rollup}.")
        rollups[identity] = (
            min((window[0] for window in windows), key=_stamp),
            max((window[1] for window in windows), key=_stamp),
        )
    return references, rollups


def _matches(task, field: str, expected, context: str) -> bool:
    try:
        return _stamp(_read(task, field, context)) == _stamp(expected)
    except ValueError:
        return False


def _verify_window(task, expected, context: str, fields=("Start", "Finish")) -> None:
    from .project import ProjectAutomationError
    for field, value in zip(fields, expected):
        if not _matches(task, field, value, context):
            raise ProjectAutomationError(
                f"Microsoft Project did not retain synchronized reference dates: {context}, field={field}, expected={value!s}."
            )


def _write(task, field: str, value, context: str, stats) -> None:
    from .project import ProjectAutomationError
    try:
        setattr(task, field, value)
    except Exception as exc:
        raise ProjectAutomationError(
            f"Microsoft Project rejected reference schedule write: {context}, field={field}: {exc}"
        ) from exc
    stats["written"] += 1


def _sync_window(task, expected, context: str, stats, fields=("Start", "Finish")) -> None:
    # Read each field immediately before its comparison. In particular a Start
    # assignment can move Finish even when Finish matched before that write.
    written_before = stats["written"]
    for field, value in zip(fields, expected):
        if _matches(task, field, value, context):
            stats["skipped"] += 1
            continue
        _write(task, field, value, context, stats)
    if stats["written"] != written_before:
        _verify_window(task, expected, context, fields)


def verify_copy_isolation(task, context, *, assignments=True):
    """Copies must never become a second resource demand or dependency driver.

    Strict reads are intentional: unknown state is not proof of isolation.
    Never erase actuals or user-owned assignments to make a copy pass.
    """
    from .project import ProjectAutomationError
    for field in ("ActualWork", "ActualDuration"):
        value = _read(task, field, context)
        if not isinstance(value, (int, float)) or value != 0:
            raise ProjectAutomationError(f"Active copy has actuals: {context}, field={field}, value={value!r}. Actuals were preserved.")
    for field in (("Assignments", "TaskDependencies") if assignments else ("TaskDependencies",)):
        collection = _read(task, field, context)
        count = _read(collection, "Count", context)
        if not isinstance(count, (int, float)) or count != 0:
            raise ProjectAutomationError(f"Active copy must have no {field}: {context}. Resolve the unexpected assignments/links before retrying.")
    if assignments:
        work = _read(task, "Work", context)
        if not isinstance(work, (int, float)) or work != 0:
            raise ProjectAutomationError(f"Active copy must have zero Work: {context}, value={work!r}.")


def synchronize_reference_dates(session, plan, config, task_by_key, summary_tasks, raw_dates) -> None:
    from .project import ProjectScanProgress, verify_project_value
    session._reference_dates_synchronized = False
    session._reference_primary_date_stamps = None
    references, rollups = _targets(plan, task_by_key, summary_tasks, raw_dates)
    # Mode changes must happen before the final calculation/date capture, never
    # during copy synchronization. A Manual summary can move children.
    for identity in rollups:
        verify_project_value(summary_tasks[identity], "Manual", False, f"rollup={identity[0]}:{identity[1]}")
    for key in references:
        task = task_by_key[key]
        verify_project_value(task, "Active", True, f"reference={key}")
        verify_project_value(task, "Manual", True, f"reference={key}")
        verify_copy_isolation(task, f"reference={key}")
    # Keep the pre-write source values. Relational checks alone would miss a
    # primary moving inside a mixed rollup's unchanged overall date envelope.
    primary_stamps = {
        key: tuple(_stamp(value) for value in raw_dates[key])
        for key in _required_primary_keys(plan)
    }
    stats = {"written": 0, "skipped": 0, "references": len(references), "rollups": len(rollups)}
    plan.stats["project_reference_dates"] = stats
    progress = ProjectScanProgress("Active copy dates", len(references))
    for index, (key, window) in enumerate(references.items(), start=1):
        task = task_by_key[key]
        _sync_window(task, window, f"reference={key}", stats)
        progress.update(index)
    session._reference_primary_date_stamps = (id(plan), primary_stamps)
    session._reference_dates_synchronized = True


def _primary_change_details(plan, primary, previous, current, task, verification_stage) -> str:
    """Describe a rejected change without additional date reads or any writes."""
    epic = next((epic for epic in plan.epics.values() if _key(epic.key) == primary), None)
    rollup = summary_id(epic.rollup_mode, epic.rollup_key) if epic is not None else "unavailable"

    def minute_text(stamp):
        return datetime(*stamp).isoformat(sep=" ", timespec="minutes") if stamp is not None else "unavailable"

    dates = "; ".join(
        f"{field}: old={minute_text(previous[index] if previous is not None else None)}, "
        f"new={minute_text(current[index])}"
        for index, field in enumerate(("Start", "Finish"))
    )
    # RecalcFlags is optional diagnostic evidence. Unsupported COM properties
    # must not replace the primary schedule mismatch with a secondary error.
    try:
        flags = int(task.RecalcFlags)
        driver = "yes" if flags & 128 else "no"  # pjDriverParentTask
        drivers = f"RecalcFlags={flags}, parent_driver={driver}"
    except Exception:
        drivers = "RecalcFlags=unavailable, parent_driver=unknown"
    return f"stage={verification_stage}, primary={primary}, rollup={rollup}; {dates}; {drivers}"


def verify_reference_dates(
    session, plan, config, task_by_key, summary_tasks, *, verification_stage="verification",
) -> int:
    from .project import ProjectAutomationError, ProjectScanProgress, verify_project_value
    # The existing verification pass supplies both indexes. Read each needed
    # driving task's live dates once; no task collection scan or cached values.
    raw_dates = {}
    required = sorted(_required_primary_keys(plan))
    progress = ProjectScanProgress("Final schedule source verification", len(required))
    for index, primary in enumerate(required, start=1):
        task = task_by_key.get(primary)
        if task is not None:
            raw_dates[primary] = (_read(task, "Start", f"primary={primary}"),
                                  _read(task, "Finish", f"primary={primary}"))
        progress.update(index)
    references, rollups = _targets(plan, task_by_key, summary_tasks, raw_dates)
    cached = getattr(session, "_reference_primary_date_stamps", None)
    if cached is not None and cached[0] == id(plan):
        for primary, window in raw_dates.items():
            current = tuple(_stamp(value) for value in window)
            previous = cached[1].get(primary)
            if current != previous:
                details = _primary_change_details(
                    plan, primary, previous, current, task_by_key[primary], verification_stage,
                )
                raise ProjectAutomationError(
                    f"Primary schedule changed after reference date synchronization: {details}. "
                    "The final autoscheduled primary dates must remain unchanged."
                )
    progress = ProjectScanProgress("Active copies and native rollup verification", len(references) + len(rollups))
    index = 0
    for key, window in references.items():
        task = task_by_key[key]
        context = f"reference={key}"
        verify_project_value(task, "Active", True, context)
        verify_project_value(task, "Manual", True, context)
        verify_copy_isolation(task, context)
        _verify_window(task, window, context)
        index += 1
        progress.update(index)
    for identity, window in rollups.items():
        task = summary_tasks[identity]
        context = f"rollup={identity[0]}:{identity[1]}"
        verify_project_value(task, "Manual", False, context)
        _verify_window(task, window, context)
        index += 1
        progress.update(index)
    return len(references) * 9 + len(rollups) * 3
