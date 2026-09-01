"""Microsoft Project automation adapter.

This module is imported on every platform, but it only imports pywin32 when a
command needs to open or write an MPP file.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import AuditItem, PlanEpic, RunPlan, ProjectTaskSnapshot, summary_id


class ProjectAutomationError(RuntimeError):
    """Raised when Microsoft Project automation is unavailable or fails."""


def prepare_sandbox_copy(main_project: Path, run_dir: Path, run_id: str) -> Path:
    if not main_project.exists():
        raise ProjectAutomationError(f"Main Project file does not exist: {main_project}")
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox_name = f"{main_project.stem}.sandbox.{run_id}{main_project.suffix}"
    sandbox_path = run_dir / sandbox_name
    shutil.copy2(str(main_project), str(sandbox_path))
    return sandbox_path


def snapshot_project_file(path: Path, config: Dict[str, Any], visible: bool = False) -> Dict[str, ProjectTaskSnapshot]:
    with MicrosoftProjectSession(visible=visible) as session:
        session.open(path)
        return session.snapshot_tasks(config)


def apply_plan_to_sandbox(
    sandbox_path: Path,
    plan: RunPlan,
    config: Dict[str, Any],
    visible: bool = False,
) -> List[AuditItem]:
    with MicrosoftProjectSession(visible=visible) as session:
        session.open(sandbox_path)
        before = session.snapshot_tasks(config)
        session.configure_custom_fields(config)
        session.apply_plan(plan, config)
        session.recalculate()
        session.add_schedule_review_items(plan, before, config)
        session.apply_review_formatting(plan, config)
        session.save()
    return plan.audit_items


def create_project_from_plan(
    output_project: Path,
    plan: RunPlan,
    config: Dict[str, Any],
    visible: bool = False,
) -> List[AuditItem]:
    with MicrosoftProjectSession(visible=visible) as session:
        session.new()
        session.configure_custom_fields(config)
        session.apply_plan(plan, config)
        session.recalculate()
        session.apply_review_formatting(plan, config)
        session.save_as(output_project)
    return plan.audit_items


class MicrosoftProjectSession:
    def __init__(self, visible: bool = False) -> None:
        if os.name != "nt":
            raise ProjectAutomationError(
                "Microsoft Project automation requires Windows, Microsoft Project desktop, and pywin32. "
                "Use 'validate' mode on non-Windows systems."
            )
        try:
            import win32com.client  # type: ignore
        except ImportError as exc:
            raise ProjectAutomationError(
                "pywin32 is required for Microsoft Project automation. Install it with: py -m pip install pywin32"
            ) from exc
        self.win32com = win32com.client
        self.visible = visible
        self.app: Any = None
        self.project: Any = None

    def __enter__(self) -> "MicrosoftProjectSession":
        self.app = self.win32com.Dispatch("MSProject.Application")
        self.app.Visible = self.visible
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self.project is not None:
                self.app.FileClose()
        finally:
            if self.app is not None and not self.visible:
                self.app.Quit()

    def open(self, path: Path) -> None:
        self.app.FileOpen(str(path))
        self.project = self.app.ActiveProject
        self.app.ViewApply(Name="&Gantt Chart")

    def new(self) -> None:
        self.app.FileNew()
        self.project = self.app.ActiveProject
        self.app.ViewApply(Name="&Gantt Chart")

    def save(self) -> None:
        self.app.FileSave()

    def save_as(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.app.FileSaveAs(str(path))

    def recalculate(self) -> None:
        try:
            self.app.CalculateProject()
        except Exception:
            try:
                self.app.CalculateAll()
            except Exception:
                pass

    def configure_custom_fields(self, config: Dict[str, Any]) -> None:
        for logical_name, project_field in config.get("project_fields", {}).items():
            friendly_name = config.get("project_field_names", {}).get(logical_name)
            if not friendly_name:
                continue
            try:
                field_id = self.app.FieldNameToFieldConstant(project_field)
                self.app.CustomFieldRename(field_id, friendly_name)
            except Exception as exc:
                raise ProjectAutomationError(
                    f"Could not configure Project custom field {project_field} as {friendly_name}: {exc}"
                ) from exc

    def snapshot_tasks(self, config: Dict[str, Any]) -> Dict[str, ProjectTaskSnapshot]:
        snapshots: Dict[str, ProjectTaskSnapshot] = {}
        fields = config.get("project_fields", {})
        for task in self.iter_tasks():
            jira_key = safe_get(task, fields.get("jira_key", "Text1"))
            j2p_key = safe_get(task, fields.get("j2p_key", "Text10"))
            rollup_key = safe_get(task, fields.get("rollup_key", "Text5"))
            snapshot_key = j2p_key or jira_key or rollup_key
            if not snapshot_key:
                continue
            snapshots[str(snapshot_key).upper()] = ProjectTaskSnapshot(
                key=str(snapshot_key).upper(),
                jira_key=str(jira_key),
                name=str(safe_get(task, "Name")),
                issue_id=str(safe_get(task, fields.get("jira_issue_id", "Text2"))),
                issue_type=str(safe_get(task, fields.get("jira_issue_type", "Text3"))),
                rollup_mode=str(safe_get(task, fields.get("rollup_mode", "Text4"))),
                rollup_key=str(rollup_key),
                resource_group=str(safe_get(task, fields.get("resource_group", "Text6"))),
                key_prefix=str(safe_get(task, fields.get("jira_key_prefix", "Text7"))),
                total_story_points=safe_float(safe_get(task, fields.get("total_story_points", "Number1"))),
                completed_story_points=safe_float(
                    safe_get(task, fields.get("completed_story_points", "Number2"))
                ),
                percent_complete=safe_int(safe_get(task, "PercentComplete")),
                status=str(safe_get(task, fields.get("jira_status", "Text9"))),
                target_start=project_date_to_iso(safe_get(task, fields.get("jira_target_start", "Date1"))),
                target_end=project_date_to_iso(safe_get(task, fields.get("jira_target_end", "Date2"))),
                start=project_date_to_iso(safe_get(task, "Start")),
                finish=project_date_to_iso(safe_get(task, "Finish")),
                predecessors=parse_project_key_list(str(safe_get(task, "Predecessors"))),
                successors=parse_project_key_list(str(safe_get(task, "Successors"))),
                row_role=str(safe_get(task, fields.get("row_role", "Text11"))),
                fix_version=str(safe_get(task, fields.get("fix_version", "Text12"))),
                drives_schedule=safe_bool(safe_get(task, fields.get("drives_schedule", "Flag4"))),
                primary_schedule_key=str(safe_get(task, fields.get("primary_schedule_key", "Text13"))),
                is_summary=bool(safe_get(task, "Summary")),
                active=safe_bool(safe_get(task, "Active")),
                source="project",
            )
        return snapshots

    def apply_plan(self, plan: RunPlan, config: Dict[str, Any]) -> None:
        self.set_auto_scheduled()
        task_by_key = self.index_tasks_by_key(config)
        summary_tasks = self.ensure_summaries(plan, config, task_by_key)
        task_by_key = self.index_tasks_by_key(config)

        for epic in sorted(plan.epics.values(), key=lambda item: (item.rollup_key, item.key)):
            parent_summary_id = summary_id(epic.rollup_mode, epic.rollup_key)
            task = task_by_key.get(epic.key)
            if task is None:
                task = self.add_epic_under_summary(epic, summary_tasks[parent_summary_id])
                task_by_key[epic.key] = task
            else:
                task = self.ensure_epic_under_summary(task, epic, summary_tasks[parent_summary_id], config, plan)
                task_by_key[epic.key] = task
            self.update_epic_task(task, epic, config)

        self.apply_dependencies(plan, task_by_key)
        self.mark_unmatched_tasks(plan, config, task_by_key)

    def set_auto_scheduled(self) -> None:
        for task in self.iter_tasks():
            try:
                task.Manual = False
            except Exception:
                pass

    def iter_tasks(self) -> List[Any]:
        tasks = []
        if self.project is None:
            return tasks
        for index in range(1, int(self.project.Tasks.Count) + 1):
            try:
                task = self.project.Tasks(index)
            except Exception:
                continue
            if task is not None:
                tasks.append(task)
        return tasks

    def index_tasks_by_key(self, config: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        key_field = config.get("project_fields", {}).get("jira_key", "Text1")
        j2p_key_field = config.get("project_fields", {}).get("j2p_key", "Text10")
        for task in self.iter_tasks():
            key = safe_get(task, j2p_key_field) or safe_get(task, key_field)
            if key:
                result[str(key).upper()] = task
        return result

    def ensure_summaries(
        self,
        plan: RunPlan,
        config: Dict[str, Any],
        task_by_key: Dict[str, Any],
    ) -> Dict[str, Any]:
        fields = config.get("project_fields", {})
        summary_tasks: Dict[str, Any] = {}
        for summary in sorted(plan.summaries.values(), key=lambda item: item.key):
            task = task_by_key.get(summary.key) if summary.rollup_mode == "initiative" else None
            if task is None:
                task = self.find_rollup_summary(summary.rollup_mode, summary.key, config)
            if task is None:
                task = self.project.Tasks.Add(summary.name)
            try:
                task.Manual = False
            except Exception:
                pass
            if summary.rollup_mode == "initiative":
                setattr(task, fields.get("jira_key", "Text1"), summary.key)
                setattr(task, fields.get("jira_issue_type", "Text3"), "Initiative")
            else:
                setattr(task, fields.get("jira_issue_type", "Text3"), "FixVersion")
            setattr(task, fields.get("rollup_mode", "Text4"), summary.rollup_mode)
            setattr(task, fields.get("rollup_key", "Text5"), summary.key)
            setattr(task, fields.get("total_story_points", "Number1"), summary.total_story_points)
            setattr(task, fields.get("completed_story_points", "Number2"), summary.completed_story_points)
            task.PercentComplete = summary.percent_complete
            summary_tasks[summary.summary_id] = task
        return summary_tasks

    def find_rollup_summary(self, rollup_mode: str, rollup_key: str, config: Dict[str, Any]) -> Optional[Any]:
        mode_field = config.get("project_fields", {}).get("rollup_mode", "Text4")
        rollup_field = config.get("project_fields", {}).get("rollup_key", "Text5")
        for task in self.iter_tasks():
            same_mode = str(safe_get(task, mode_field)) == rollup_mode
            same_key = str(safe_get(task, rollup_field)).upper() == rollup_key.upper()
            if same_mode and same_key:
                return task
        return None

    def add_epic_under_summary(self, epic: PlanEpic, summary_task: Any) -> Any:
        before = int(summary_task.ID) + 1
        task = self.project.Tasks.Add(epic.summary, before)
        try:
            task.OutlineIndent()
        except Exception:
            try:
                self.app.SelectRow(Row=int(task.ID), RowRelative=False)
                self.app.OutlineIndent(1)
            except Exception:
                pass
        return task

    def ensure_epic_under_summary(
        self,
        task: Any,
        epic: PlanEpic,
        summary_task: Any,
        config: Dict[str, Any],
        plan: RunPlan,
    ) -> Any:
        current_parent = safe_get(task, "OutlineParent")
        rollup_field = config.get("project_fields", {}).get("rollup_key", "Text5")
        current_rollup = ""
        if current_parent:
            current_rollup = str(safe_get(current_parent, rollup_field))
        if current_rollup.upper() == epic.rollup_key.upper():
            return task

        try:
            self.app.SelectRow(Row=int(task.ID), RowRelative=False)
            self.app.EditCut()
            self.app.SelectRow(Row=int(summary_task.ID) + 1, RowRelative=False)
            self.app.EditPaste()
            moved_task = self.index_tasks_by_key(config).get(epic.key, task)
            try:
                moved_task.OutlineIndent()
            except Exception:
                self.app.SelectRow(Row=int(moved_task.ID), RowRelative=False)
                self.app.OutlineIndent(1)
            return moved_task
        except Exception as exc:
            plan.audit_items.append(
                AuditItem(
                    "Warning",
                    "ProjectRollupMoveFailed",
                    jira_key=epic.key,
                    issue_type="Epic",
                    summary=epic.summary,
                    field="Rollup Key",
                    old_value=current_rollup,
                    new_value=epic.rollup_key,
                    color="review_needed",
                    message=f"Could not move the Project task under the requested rollup: {exc}",
                    reviewer_action="Move this task manually in the sandbox or rerun after correcting the Project outline.",
                )
            )
            return task

    def update_epic_task(self, task: Any, epic: PlanEpic, config: Dict[str, Any]) -> None:
        fields = config.get("project_fields", {})
        try:
            task.Manual = False
        except Exception:
            pass
        task.Name = epic.summary
        task.PercentComplete = epic.percent_complete
        setattr(task, fields.get("jira_key", "Text1"), epic.jira_key or epic.key)
        setattr(task, fields.get("jira_issue_id", "Text2"), epic.issue_id)
        setattr(task, fields.get("jira_issue_type", "Text3"), "Epic")
        setattr(task, fields.get("rollup_mode", "Text4"), epic.rollup_mode)
        setattr(task, fields.get("rollup_key", "Text5"), epic.rollup_key)
        setattr(task, fields.get("resource_group", "Text6"), epic.resource_group)
        setattr(task, fields.get("jira_key_prefix", "Text7"), epic.key_prefix)
        setattr(task, fields.get("dependency_review", "Text8"), epic.dependency_review)
        setattr(task, fields.get("jira_status", "Text9"), epic.status)
        setattr(task, fields.get("j2p_key", "Text10"), epic.key)
        setattr(task, fields.get("row_role", "Text11"), epic.row_role)
        setattr(task, fields.get("fix_version", "Text12"), epic.fix_version)
        setattr(task, fields.get("primary_schedule_key", "Text13"), epic.primary_schedule_key)
        setattr(task, fields.get("total_story_points", "Number1"), epic.total_story_points)
        setattr(task, fields.get("completed_story_points", "Number2"), epic.completed_story_points)
        setattr(task, fields.get("in_planning", "Flag1"), bool(epic.in_planning))
        setattr(task, fields.get("dependency_review_needed", "Flag3"), bool(epic.dependency_review))
        setattr(task, fields.get("drives_schedule", "Flag4"), bool(epic.drives_schedule))
        if epic.target_start:
            setattr(task, fields.get("jira_target_start", "Date1"), epic.target_start)
            try:
                task.Start = epic.target_start
            except Exception:
                pass
        if epic.target_end:
            setattr(task, fields.get("jira_target_end", "Date2"), epic.target_end)
            try:
                task.Finish = epic.target_end
            except Exception:
                pass
        if epic.completed:
            try:
                task.Active = False
            except Exception:
                pass
            try:
                task.HideBar = True
            except Exception:
                pass
        elif not epic.drives_schedule:
            try:
                task.Active = False
            except Exception:
                pass
        else:
            try:
                task.Active = True
            except Exception:
                pass
            try:
                task.HideBar = False
            except Exception:
                pass

    def apply_dependencies(self, plan: RunPlan, task_by_key: Dict[str, Any]) -> None:
        for epic in plan.epics.values():
            if not epic.drives_schedule:
                continue
            task = task_by_key.get(epic.key)
            if task is None:
                continue
            predecessor_ids = []
            for predecessor_key in epic.predecessors:
                predecessor_task = task_by_key.get(predecessor_key)
                if predecessor_task is not None:
                    predecessor_ids.append(str(predecessor_task.ID))
            try:
                task.Predecessors = ",".join(predecessor_ids)
            except Exception as exc:
                plan.audit_items.append(
                    AuditItem(
                        "Warning",
                        "ProjectDependencyWriteFailed",
                        jira_key=epic.key,
                        field="Predecessors",
                        color="dependency_review",
                        message=f"Could not write predecessors to Microsoft Project: {exc}",
                        reviewer_action="Review dependency links manually in the sandbox file.",
                    )
                )

    def mark_unmatched_tasks(
        self,
        plan: RunPlan,
        config: Dict[str, Any],
        task_by_key: Dict[str, Any],
    ) -> None:
        flag_field = config.get("project_fields", {}).get("unmatched_project_task", "Flag2")
        planned_keys = (
            set(plan.epics)
            | set(plan.summaries)
            | {summary.key.upper() for summary in plan.summaries.values()}
        )
        for key, task in task_by_key.items():
            if key in planned_keys:
                try:
                    setattr(task, flag_field, False)
                except Exception:
                    pass
                continue
            try:
                setattr(task, flag_field, True)
            except Exception:
                pass

    def add_schedule_review_items(
        self,
        plan: RunPlan,
        before: Dict[str, ProjectTaskSnapshot],
        config: Dict[str, Any],
    ) -> None:
        after = self.snapshot_tasks(config)
        changed_finishes = []
        for key, epic in plan.epics.items():
            if not epic.drives_schedule:
                continue
            before_finish = before.get(key).finish if key in before else ""
            after_finish = after.get(key).finish if key in after else ""
            if before_finish and after_finish and before_finish != after_finish:
                changed_finishes.append((key, before_finish, after_finish, self.task_is_critical(key, config)))
            if epic.target_end and after_finish and after_finish != epic.target_end:
                plan.audit_items.append(
                    AuditItem(
                        "Review",
                        "ScheduledDateMismatch",
                        jira_key=key,
                        issue_type="Epic",
                        summary=epic.summary,
                        field="Finish",
                        old_value=epic.target_end,
                        new_value=after_finish,
                        color="review_needed",
                        message="Auto-scheduled Project finish does not match Jira Target end.",
                        reviewer_action="Review schedule drivers and decide whether Project or Jira should be adjusted.",
                    )
                )

        if not changed_finishes:
            return
        changed_finishes.sort(key=lambda item: (not item[3], item[2], item[0]))
        root_key, old_finish, new_finish, _critical = changed_finishes[0]
        root_epic = plan.epics.get(root_key)
        plan.audit_items.append(
            AuditItem(
                "Review",
                "CriticalPathCascadeRoot",
                jira_key=root_key,
                issue_type="Epic",
                summary=root_epic.summary if root_epic else "",
                field="Finish",
                old_value=old_finish,
                new_value=new_finish,
                color="cascade_root",
                message="First detected critical-path end-date shift after auto-scheduling.",
                reviewer_action="Review this red finish date as the likely root schedule driver.",
            )
        )
        for key, old_finish, new_finish, _critical in changed_finishes[1:]:
            epic = plan.epics.get(key)
            plan.audit_items.append(
                AuditItem(
                    "Info",
                    "CascadingDateChange",
                    jira_key=key,
                    issue_type="Epic",
                    summary=epic.summary if epic else "",
                    field="Finish",
                    old_value=old_finish,
                    new_value=new_finish,
                    color="changed_cell",
                    message="Finish date changed after auto-scheduling.",
                    reviewer_action="Review as a downstream schedule change.",
                )
            )

    def task_is_critical(self, key: str, config: Dict[str, Any]) -> bool:
        task = self.index_tasks_by_key(config).get(key)
        if task is None:
            return False
        try:
            return bool(task.Critical)
        except Exception:
            return False

    def apply_review_formatting(self, plan: RunPlan, config: Dict[str, Any]) -> None:
        task_by_key = self.index_tasks_by_key(config)
        for item in plan.audit_items:
            if not item.jira_key or not item.color:
                continue
            lookup_key = (item.schedule_key or item.jira_key).upper()
            task = task_by_key.get(lookup_key)
            if task is None:
                continue
            column = project_column_for_audit_field(item.field, config)
            if not column:
                continue
            color = config.get("colors", {}).get(item.color, item.color)
            try:
                self.app.SelectTaskField(Row=int(task.ID), Column=column, RowRelative=False)
                self.app.Font32Ex(CellColor=project_color(color))
            except Exception:
                continue


def project_column_for_audit_field(field_name: str, config: Dict[str, Any]) -> str:
    names = config.get("project_field_names", {})
    mapping = {
        "Name": "Name",
        "% Complete": "% Complete",
        "Predecessors": "Predecessors",
        "Successors": "Successors",
        "Finish": "Finish",
        "Start": "Start",
        "Resource Group": names.get("resource_group", "Resource Group"),
        "Rollup Key": names.get("rollup_key", "Rollup Key"),
        "Schedule Key": names.get("j2p_key", "j2p Unique Key"),
        "Row Role": names.get("row_role", "j2p Row Role"),
        "Fix Version": names.get("fix_version", "Jira Fix Version"),
        "Drives Schedule": names.get("drives_schedule", "Drives Schedule"),
        "Primary Schedule Key": names.get("primary_schedule_key", "Primary Schedule Key"),
        "Total Story Points": names.get("total_story_points", "Total Story Points"),
        "Completed Story Points": names.get("completed_story_points", "Completed Story Points"),
        "In Planning": names.get("in_planning", "In Planning"),
        "Dependency Review": names.get("dependency_review", "Dependency Review"),
        "Jira Target Start": names.get("jira_target_start", "Jira Target Start"),
        "Jira Target End": names.get("jira_target_end", "Jira Target End"),
    }
    return mapping.get(field_name, "")


def safe_get(task: Any, name: str) -> Any:
    try:
        return getattr(task, name)
    except Exception:
        return ""


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def safe_bool(value: Any) -> Optional[bool]:
    if value in ("", None):
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "no", "0"}:
            return False
        if normalized in {"true", "yes", "1"}:
            return True
    return bool(value)


def project_date_to_iso(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    text = str(value)
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.split()[0], fmt).date().isoformat()
        except ValueError:
            pass
    return text


def parse_project_key_list(value: str) -> List[str]:
    # Project predecessor strings are often row IDs rather than Jira keys. Keep
    # only Jira-looking tokens when custom views include them.
    import re

    return sorted(set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", value.upper())))


def project_color(hex_color: str) -> int:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        return -16777216
    red = int(cleaned[0:2], 16)
    green = int(cleaned[2:4], 16)
    blue = int(cleaned[4:6], 16)
    return (blue << 16) + (green << 8) + red
