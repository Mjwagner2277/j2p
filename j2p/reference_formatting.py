"""Cosmetic reference-row formatting without changing task activation or data."""

from __future__ import annotations

from typing import Any, Dict

from .models import AuditItem, RunPlan


def format_reference_rows(session: Any, plan: RunPlan, task_by_key: Dict[str, Any]) -> None:
    """Remove legacy strike-through from selected active copies when supported.

    A successful command is not a readback of the rendered font: Project's Cell
    object has no documented strike-through property. Active remains mandatory;
    formatting failures are reported without changing task state or other styles.
    """
    # The adapter calls this helper after preparing its Gantt review view. Import
    # its COM helpers lazily so neither module needs a Windows-only dependency.
    from .project import (
        ProjectScanProgress, project_call_failed, project_task_identity,
        safe_bool, safe_get, safe_int,
    )

    references = [epic for epic in plan.epics.values() if not epic.drives_schedule]
    counts = {"total": len(references), "applied": 0, "failed": 0}
    plan.stats["project_reference_formatting"] = counts
    if not references:
        return
    progress = ProjectScanProgress("Reference row formatting", len(references), every=50)
    # Walk the native sheet in row order instead of jumping between rollups in
    # plan insertion order. Reuse the resolved task and ID during selection.
    entries = []
    for epic in references:
        task = task_by_key.get(epic.key.upper())
        try:
            row = safe_int(safe_get(task, "ID")) if task is not None else 0
        except Exception:
            row = 0  # Invalid identifiers still produce a cosmetic warning below.
        entries.append((row, epic.key, epic, task))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    examples = []
    for index, (row, _key, epic, task) in enumerate(entries, start=1):
        try:
            if task is None:
                raise ValueError("Reference task was not found in the formatting index.")
            if row <= 0:
                raise ValueError("Reference task has no positive Project row ID.")
            expected_identity = project_task_identity(task)
            if safe_bool(safe_get(task, "Active")) is not True:
                raise ValueError("Reference task could not be confirmed active; formatting skipped.")
            result = session.app.SelectRow(
                Row=row, RowRelative=False, Height=0, Extend=False, Add=False,
            )
            if project_call_failed(result):
                raise ValueError("Project rejected reference-row selection.")
            selected_task = safe_get(safe_get(session.app, "ActiveCell"), "Task")
            if project_task_identity(selected_task) != expected_identity:
                raise ValueError("Selected Project task does not match the reference; formatting skipped.")
            # Explicit False removes rather than toggles. This dedicated command
            # avoids optional font arguments and leaves review cell colors alone.
            result = session.app.FontStrikethrough(False)
            if project_call_failed(result):
                raise ValueError("Project rejected removing reference-row strike-through.")
            if safe_bool(safe_get(task, "Active")) is not True:
                raise ValueError("Reference task could not be confirmed active after formatting.")
            counts["applied"] += 1
        except Exception as exc:
            counts["failed"] += 1
            if len(examples) < 5:
                examples.append(f"{epic.key}: {str(exc)[:250]}")
        finally:
            progress.update(index)
    if counts["failed"]:
        plan.audit_items.append(AuditItem(
            severity="Warning", category="ProjectReferenceFormattingFailed",
            field="Reference Formatting", color="review_needed",
            message=(
                f"Could not apply reference-row appearance to {counts['failed']} of "
                f"{counts['total']} reference task(s). " + " | ".join(examples)
            ),
            reviewer_action=(
                "Use j2p Row Role to identify Reference rows (active copies). Check Project's "
                "text style if legacy strike-through remains; keep copies active. "
                "The formatting pass does not change task activation or schedule values."
            ),
        ))
