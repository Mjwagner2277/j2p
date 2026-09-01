"""Microsoft Project automation adapter.

This module is imported on every platform, but it only imports pywin32 when a
command needs to open or write an MPP file.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import AuditItem, PlanEpic, ProjectTaskSnapshot, RunPlan
from .rollups import summary_id


PROJECT_TASK_MANAGER_RESOLUTION = (
    "Close Microsoft Project and rerun j2p. If the problem continues, open Windows Task Manager "
    "(Ctrl+Shift+Esc), find Microsoft Project or WINPROJ.EXE on the Processes or Details tab, "
    "choose End task, then rerun j2p."
)

J2P_REVIEW_TABLE_NAME = "j2p Review"
PROJECT_SOLID_FILL_PATTERN = 1
PJ_COLOR_RED = 1
PJ_COLOR_YELLOW = 2
PJ_COLOR_LIME = 3
PJ_COLOR_AQUA = 4
PJ_COLOR_BLUE = 5
PJ_COLOR_GRAY = 14
PJ_COLOR_SILVER = 15
PROJECT_ENTRY_TABLE_COLUMNS = {
    "duration",
    "finish",
    "id",
    "indicators",
    "name",
    "predecessors",
    "resource names",
    "start",
    "task mode",
}


def project_progress(message: str) -> None:
    print(f"[j2p] {datetime.now().strftime('%H:%M:%S')} {message}", flush=True)


class ProjectAutomationError(RuntimeError):
    """Raised when Microsoft Project automation is unavailable or fails."""


def cascade_branch_driver_keys(plan: RunPlan, changed_keys: set[str]) -> set[str]:
    drivers: set[str] = set()
    for key in changed_keys:
        epic = plan.epics.get(key)
        if not epic:
            continue
        if any(successor_key in changed_keys for successor_key in epic.successors):
            drivers.add(key)
    return drivers


def prepare_sandbox_copy(main_project: Path, run_dir: Path, run_id: str) -> Path:
    main_project = main_project.expanduser().resolve()
    run_dir = run_dir.expanduser().resolve()
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
    dependency_write_mode: str = "fast",
) -> List[AuditItem]:
    with MicrosoftProjectSession(visible=visible) as session:
        project_progress(f"Opening sandbox MPP: {sandbox_path}")
        session.open(sandbox_path)
        project_progress("Reading existing Project task state")
        before = session.snapshot_tasks(config)
        project_progress("Configuring Project custom fields")
        session.configure_custom_fields(config)
        project_progress("Applying Jira updates to Project rows")
        session.apply_plan(plan, config, dependency_write_mode=dependency_write_mode)
        project_progress("Recalculating Project after Jira updates")
        session.recalculate()
        project_progress("Analyzing schedule date changes")
        session.add_schedule_review_items(plan, before, config)
        project_progress("Applying Project review table and cell colors")
        session.apply_review_formatting(plan, config)
        project_progress("Saving sandbox MPP")
        session.save()
        project_progress("Finished Project sandbox update")
    return plan.audit_items


def create_project_from_plan(
    output_project: Path,
    plan: RunPlan,
    config: Dict[str, Any],
    visible: bool = False,
    dependency_write_mode: str = "fast",
) -> List[AuditItem]:
    with MicrosoftProjectSession(visible=visible) as session:
        project_progress("Creating blank Microsoft Project file")
        session.new()
        project_progress("Configuring Project custom fields")
        session.configure_custom_fields(config)
        project_progress("Creating initial Project rows from Jira")
        session.apply_plan(plan, config, write_dependencies=False)
        project_progress("Recalculating initial Project schedule")
        session.recalculate()
        project_progress(f"Saving initial sandbox MPP: {output_project}")
        session.save_as(output_project)
        project_progress("Writing Project predecessor links")
        session.apply_plan_dependencies(plan, config, dependency_write_mode)
        project_progress("Recalculating Project after predecessor links")
        session.recalculate()
        project_progress("Applying Project review table and cell colors")
        session.apply_review_formatting(plan, config)
        project_progress("Saving initial sandbox MPP")
        session.save()
        project_progress("Finished initial Project file creation")
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
            import pythoncom  # type: ignore
        except ImportError as exc:
            raise ProjectAutomationError(
                "pywin32 is required for Microsoft Project automation. Install it with: py -m pip install pywin32"
            ) from exc
        self.win32com = win32com.client
        self.pythoncom = pythoncom
        self.visible = visible
        self.app: Any = None
        self.project: Any = None
        self.saved_successfully = False
        self.owns_app = False
        self.com_initialized = False

    def __enter__(self) -> "MicrosoftProjectSession":
        try:
            self.pythoncom.CoInitialize()
            self.com_initialized = True
        except Exception:
            pass
        self.app = self.create_application()
        self.configure_application_window()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self.project is not None:
                self.close_project(save_changes=exc_type is None and self.saved_successfully)
        finally:
            if self.app is not None and self.owns_app and not self.visible:
                try:
                    self.app.Quit()
                except Exception:
                    pass
            if self.com_initialized:
                try:
                    self.pythoncom.CoUninitialize()
                except Exception:
                    pass

    def create_application(self) -> Any:
        errors = []
        try:
            app = self.win32com.DispatchEx("MSProject.Application")
            self.owns_app = True
            return app
        except Exception as exc:
            errors.append(f"DispatchEx failed: {exc}")
        try:
            app = self.win32com.Dispatch("MSProject.Application")
            self.owns_app = False
            return app
        except Exception as exc:
            errors.append(f"Dispatch failed: {exc}")
        detail = " | ".join(errors) if errors else "no COM startup method was available"
        raise ProjectAutomationError(
            "Could not start Microsoft Project through COM. "
            f"{PROJECT_TASK_MANAGER_RESOLUTION} Last Project error: {detail}"
        )

    def configure_application_window(self) -> None:
        try:
            self.app.Visible = self.visible
        except Exception:
            pass
        try:
            self.app.DisplayAlerts = False
        except Exception:
            pass

    def open(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if not path.exists():
            raise ProjectAutomationError(f"Project file does not exist: {path}")
        try:
            self.app.FileOpen(Name=str(path))
        except Exception:
            self.app.FileOpen(str(path))
        self.project = self.app.ActiveProject
        self.apply_gantt_chart_view()
        project_progress("Project file opened")

    def new(self) -> None:
        self.project = self.create_blank_project()
        if self.project is None:
            self.project = safe_get(self.app, "ActiveProject")
        if not self.project:
            raise ProjectAutomationError(
                "Microsoft Project did not return an active blank project after creating a new file. "
                f"{PROJECT_TASK_MANAGER_RESOLUTION}"
            )
        self.apply_gantt_chart_view()
        project_progress("Blank Project file ready")

    def create_blank_project(self) -> Any:
        errors = []
        projects = safe_get(self.app, "Projects")
        if projects:
            for create_project in (
                lambda: projects.Add(DisplayProjectInfo=False, Template="", FileNewDialog=False),
                lambda: projects.Add(False, "", False),
                lambda: projects.Add(False),
            ):
                try:
                    project = create_project()
                    return project or safe_get(self.app, "ActiveProject")
                except Exception as exc:
                    errors.append(str(exc))

        for create_project in (
            lambda: self.app.FileNew(
                SummaryInfo=False,
                Template="",
                FileNewDialog=False,
                FileNewWorkpane=False,
            ),
            lambda: self.app.FileNew(False, "", False, False),
            lambda: self.app.FileNew(),
        ):
            try:
                create_project()
                project = safe_get(self.app, "ActiveProject")
                if project:
                    return project
            except Exception as exc:
                errors.append(str(exc))

        detail = errors[-1] if errors else "no Project creation method was available"
        raise ProjectAutomationError(
            "Could not create a blank Microsoft Project file through COM. "
            "This can happen when Project is not fully initialized, a Project startup/template dialog is open, "
            "or the installed Project edition blocks blank-file automation. "
            f"{PROJECT_TASK_MANAGER_RESOLUTION} "
            f"Last Project error: {detail}"
        )

    def apply_gantt_chart_view(self) -> None:
        try:
            self.app.ViewApply(Name="&Gantt Chart")
            return
        except Exception:
            pass
        try:
            self.app.ViewApply("&Gantt Chart")
        except Exception:
            pass

    def save(self) -> None:
        self.app.FileSave()
        self.saved_successfully = True
        project_progress("Project save complete")

    def save_as(self, path: Path) -> None:
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.app.FileSaveAs(Name=str(path))
        except Exception:
            self.app.FileSaveAs(str(path))
        self.saved_successfully = True
        project_progress("Project Save As complete")

    def close_project(self, save_changes: bool) -> None:
        save_option = 1 if save_changes else 0
        try:
            self.app.FileCloseEx(Save=save_option, NoAuto=True, CheckIn=False)
            return
        except Exception:
            pass
        try:
            self.app.FileCloseEx(save_option, True, False)
            return
        except Exception:
            pass
        try:
            self.app.FileClose(Save=save_option)
            return
        except Exception:
            pass
        self.app.FileClose()

    def recalculate(self) -> None:
        project_progress("Project recalculation started")
        try:
            self.app.CalculateProject()
            project_progress("Project recalculation complete")
        except Exception:
            try:
                self.app.CalculateAll()
                project_progress("Project recalculation complete")
            except Exception:
                project_progress("Project recalculation command was rejected; continuing")
                pass

    def configure_custom_fields(self, config: Dict[str, Any]) -> None:
        configured = [
            (logical_name, project_field)
            for logical_name, project_field in config.get("project_fields", {}).items()
            if config.get("project_field_names", {}).get(logical_name)
        ]
        if configured:
            project_progress(f"Configuring {len(configured)} named custom Project field(s)")
        for logical_name, project_field in configured:
            friendly_name = config.get("project_field_names", {}).get(logical_name)
            try:
                field_id = self.app.FieldNameToFieldConstant(project_field)
                self.app.CustomFieldRename(field_id, friendly_name)
            except Exception as exc:
                raise ProjectAutomationError(
                    f"Could not configure Project custom field {project_field} as {friendly_name}: {exc}"
                ) from exc
        if configured:
            project_progress("Custom Project field configuration complete")

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
                resource_group=self.get_native_resource_group(task),
                key_prefix=str(safe_get(task, fields.get("jira_key_prefix", "Text7"))),
                total_story_points=safe_float(safe_get(task, fields.get("total_story_points", "Number1"))),
                completed_story_points=safe_float(
                    safe_get(task, fields.get("completed_story_points", "Number2"))
                ),
                logged_hours=safe_float(safe_get(task, fields.get("logged_hours", "Number3"))),
                story_point_ratio=safe_float(safe_get(task, story_point_ratio_project_field(config))),
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

    def apply_plan(
        self,
        plan: RunPlan,
        config: Dict[str, Any],
        write_dependencies: bool = True,
        dependency_write_mode: str = "fast",
    ) -> None:
        project_progress("Setting existing Project tasks to auto scheduled")
        self.set_auto_scheduled()
        project_progress("Indexing Project tasks by Jira key")
        task_by_key = self.index_tasks_by_key(config)
        project_progress("Ensuring rollup summary rows")
        summary_tasks = self.ensure_summaries(plan, config, task_by_key)
        task_by_key = self.index_tasks_by_key(config)

        epics = sorted(plan.epics.values(), key=lambda item: (item.rollup_key, item.key))
        total_epics = len(epics)
        if total_epics:
            project_progress(f"Writing {total_epics} epic row(s)")
        for index, epic in enumerate(epics, start=1):
            if index == 1 or index % 50 == 0 or index == total_epics:
                project_progress(f"Epic row write progress: {index}/{total_epics} row(s)")
            parent_summary_id = summary_id(epic.rollup_mode, epic.rollup_key)
            task = task_by_key.get(epic.key)
            if task is None:
                task = self.add_epic_under_summary(epic, summary_tasks[parent_summary_id])
                task_by_key[epic.key] = task
            else:
                task = self.ensure_epic_under_summary(task, epic, summary_tasks[parent_summary_id], config, plan)
                task_by_key[epic.key] = task
            self.update_epic_task(task, epic, config, plan)
        if total_epics:
            project_progress("Epic row writes complete")

        if not write_dependencies:
            task_by_key = self.index_tasks_by_key(config)
            project_progress("Marking Project rows that no longer match Jira")
            self.mark_unmatched_tasks(plan, config, task_by_key)
            return
        self.apply_plan_dependencies(plan, config, dependency_write_mode)

    def apply_plan_dependencies(
        self,
        plan: RunPlan,
        config: Dict[str, Any],
        dependency_write_mode: str = "fast",
    ) -> None:
        project_progress("Recalculating before dependency write")
        self.recalculate()
        project_progress("Indexing Project tasks for dependency write")
        task_by_key = self.index_tasks_by_key(config)
        self.apply_dependencies(plan, task_by_key, dependency_write_mode)
        project_progress("Marking Project rows that no longer match Jira")
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
            setattr(task, fields.get("logged_hours", "Number3"), summary.logged_hours)
            setattr(task, story_point_ratio_project_field(config), summary.story_point_ratio)
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

    def update_epic_task(self, task: Any, epic: PlanEpic, config: Dict[str, Any], plan: RunPlan) -> None:
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
        self.set_native_resource_group(task, epic.resource_group)
        setattr(task, fields.get("jira_key_prefix", "Text7"), epic.key_prefix)
        setattr(task, fields.get("dependency_review", "Text8"), epic.dependency_review)
        setattr(task, fields.get("jira_status", "Text9"), epic.status)
        setattr(task, fields.get("j2p_key", "Text10"), epic.key)
        setattr(task, fields.get("row_role", "Text11"), epic.row_role)
        setattr(task, fields.get("fix_version", "Text12"), epic.fix_version)
        setattr(task, fields.get("primary_schedule_key", "Text13"), epic.primary_schedule_key)
        setattr(task, fields.get("total_story_points", "Number1"), epic.total_story_points)
        setattr(task, fields.get("completed_story_points", "Number2"), epic.completed_story_points)
        setattr(task, fields.get("logged_hours", "Number3"), epic.logged_hours)
        setattr(task, story_point_ratio_project_field(config), epic.story_point_ratio)
        setattr(task, fields.get("in_planning", "Flag1"), bool(epic.in_planning))
        setattr(task, fields.get("dependency_review_needed", "Flag3"), bool(epic.dependency_review))
        setattr(task, fields.get("drives_schedule", "Flag4"), bool(epic.drives_schedule))
        self.write_project_date(
            task,
            epic,
            plan,
            fields.get("jira_target_start", "Date1"),
            "Jira Target Start",
            "Start",
        )
        self.write_project_date(
            task,
            epic,
            plan,
            fields.get("jira_target_end", "Date2"),
            "Jira Target End",
            "Finish",
        )
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

    def write_project_date(
        self,
        task: Any,
        epic: PlanEpic,
        plan: RunPlan,
        field_name: str,
        audit_field: str,
        schedule_attribute: str,
    ) -> None:
        date_text = epic.target_start if audit_field == "Jira Target Start" else epic.target_end
        if not date_text:
            return
        try:
            project_date = project_date_for_com(date_text, schedule_attribute)
        except ValueError as exc:
            plan.audit_items.append(
                AuditItem(
                    "Warning",
                    "ProjectDateRejected",
                    jira_key=epic.jira_key or epic.key,
                    schedule_key=epic.key,
                    issue_type="Epic",
                    summary=epic.summary,
                    field=audit_field,
                    new_value=date_text,
                    color="review_needed",
                    message=str(exc),
                    reviewer_action="Correct the Jira date or update the Project date manually in the sandbox.",
                    source_row=epic.source_row,
                )
            )
            return

        if not safe_set(task, field_name, project_date):
            plan.audit_items.append(
                AuditItem(
                    "Warning",
                    "ProjectDateWriteFailed",
                    jira_key=epic.jira_key or epic.key,
                    schedule_key=epic.key,
                    issue_type="Epic",
                    summary=epic.summary,
                    field=audit_field,
                    new_value=date_text,
                    color="review_needed",
                    message=f"Microsoft Project rejected the {audit_field} value for custom field {field_name}.",
                    reviewer_action="Review this Jira date and update the Project field manually if needed.",
                    source_row=epic.source_row,
                )
            )

        if safe_set(task, schedule_attribute, project_date):
            return
        plan.audit_items.append(
            AuditItem(
                "Warning",
                "ProjectScheduleDateWriteFailed",
                jira_key=epic.jira_key or epic.key,
                schedule_key=epic.key,
                issue_type="Epic",
                summary=epic.summary,
                field="Start" if schedule_attribute == "Start" else "Finish",
                new_value=date_text,
                color="review_needed",
                message=f"Microsoft Project rejected the task {schedule_attribute} value.",
                reviewer_action="Review schedule constraints, calendar settings, and the Jira date before accepting the sandbox.",
                source_row=epic.source_row,
            )
        )

    def get_native_resource_group(self, task: Any) -> str:
        value = safe_get(task, "ResourceGroup")
        if value:
            return str(value)
        try:
            field_id = self.app.FieldNameToFieldConstant("Resource Group")
            return str(task.GetField(field_id))
        except Exception:
            return ""

    def set_native_resource_group(self, task: Any, resource_group: str) -> None:
        if not resource_group:
            return
        resource = self.ensure_group_resource(resource_group)
        if self.task_has_resource(task, resource):
            return
        if self.assign_resource_to_task(task, resource):
            return
        raise ProjectAutomationError(
            "Could not populate the native Microsoft Project Resource Group field. "
            "Project calculates that task field from assigned resources, and j2p could not assign "
            f"the resource group placeholder '{resource_group}'."
        )

    def ensure_group_resource(self, resource_group: str) -> Any:
        resource = self.find_resource(resource_group)
        if resource is None:
            try:
                resource = self.project.Resources.Add(resource_group)
            except Exception as exc:
                raise ProjectAutomationError(
                    f"Could not create Microsoft Project resource '{resource_group}' for Resource Group mapping."
                ) from exc
        try:
            resource.Group = resource_group
        except Exception as exc:
            raise ProjectAutomationError(
                f"Could not set Microsoft Project resource group for resource '{resource_group}'."
            ) from exc
        return resource

    def find_resource(self, resource_name: str) -> Optional[Any]:
        if self.project is None:
            return None
        try:
            count = int(self.project.Resources.Count)
        except Exception:
            return None
        for index in range(1, count + 1):
            try:
                resource = self.project.Resources(index)
            except Exception:
                continue
            if resource is not None and str(safe_get(resource, "Name")) == resource_name:
                return resource
        return None

    def task_has_resource(self, task: Any, resource: Any) -> bool:
        try:
            resource_id = int(resource.ID)
            for index in range(1, int(task.Assignments.Count) + 1):
                assignment = task.Assignments(index)
                if int(safe_get(assignment, "ResourceID")) == resource_id:
                    return True
        except Exception:
            pass
        current_names = [name.strip() for name in str(safe_get(task, "ResourceNames")).split(",")]
        return str(safe_get(resource, "Name")) in current_names

    def assign_resource_to_task(self, task: Any, resource: Any) -> bool:
        try:
            task.Assignments.Add(ResourceID=int(resource.ID))
            return True
        except Exception:
            pass
        try:
            self.project.Assignments.Add(TaskID=int(task.ID), ResourceID=int(resource.ID))
            return True
        except Exception:
            pass
        try:
            resource_name = str(safe_get(resource, "Name"))
            current = str(safe_get(task, "ResourceNames")).strip()
            task.ResourceNames = append_resource_name(current, resource_name)
            return True
        except Exception:
            return False

    def apply_dependencies(
        self,
        plan: RunPlan,
        task_by_key: Dict[str, Any],
        dependency_write_mode: str = "fast",
    ) -> None:
        writable_items: List[Tuple[PlanEpic, Any]] = []
        for epic in plan.epics.values():
            if not epic.drives_schedule:
                continue
            task = task_by_key.get(epic.key)
            if task is None:
                continue
            if epic.predecessors or project_task_has_predecessors(task):
                writable_items.append((epic, task))
        total = len(writable_items)
        if total:
            project_progress(
                f"Writing Project predecessor fields for {total} task(s) using {dependency_write_mode} mode"
            )
        processed = 0
        for epic, task in writable_items:
            processed += 1
            if processed == 1 or processed % 25 == 0 or processed == total:
                project_progress(f"Project predecessor write progress: {processed}/{total} task(s)")
            predecessor_ids: List[str] = []
            predecessor_tasks: List[Any] = []
            missing_predecessors: List[str] = []
            for predecessor_key in epic.predecessors:
                predecessor_task = task_by_key.get(predecessor_key.upper())
                if predecessor_task is not None:
                    predecessor_id = safe_int(safe_get(predecessor_task, "ID"))
                    if predecessor_id > 0:
                        predecessor_ids.append(str(predecessor_id))
                        predecessor_tasks.append(predecessor_task)
                    else:
                        missing_predecessors.append(predecessor_key)
                else:
                    missing_predecessors.append(predecessor_key)
            for predecessor_key in missing_predecessors:
                plan.audit_items.append(
                    AuditItem(
                        "Warning",
                        "ProjectDependencyTaskMissing",
                        jira_key=epic.jira_key or epic.key,
                        schedule_key=epic.key,
                        issue_type="Epic",
                        summary=epic.summary,
                        field="Predecessors",
                        new_value=predecessor_key,
                        color="dependency_review",
                        message=(
                            f"Could not find the included Project task for predecessor schedule key "
                            f"'{predecessor_key}' while writing dependencies."
                        ),
                        reviewer_action="Review dependency links manually in the sandbox file.",
                        source_row=epic.source_row,
                    )
                )

            desired = ",".join(predecessor_ids)
            error = self.write_project_predecessors(
                task,
                desired,
                predecessor_ids,
                predecessor_tasks,
                dependency_write_mode,
            )
            if error:
                plan.audit_items.append(
                    AuditItem(
                        "Warning",
                        "ProjectDependencyWriteFailed",
                        jira_key=epic.jira_key or epic.key,
                        schedule_key=epic.key,
                        issue_type="Epic",
                        summary=epic.summary,
                        field="Predecessors",
                        new_value=desired,
                        color="dependency_review",
                        message=error,
                        reviewer_action="Review dependency links manually in the sandbox file.",
                        source_row=epic.source_row,
                    )
                )
        if total:
            project_progress("Project predecessor writes complete")

    def write_project_predecessors(
        self,
        task: Any,
        predecessor_text: str,
        expected_ids: List[str],
        predecessor_tasks: Optional[List[Any]] = None,
        dependency_write_mode: str = "fast",
    ) -> str:
        if not verify_project_predecessors(task, expected_ids):
            return ""
        if dependency_write_mode == "diagnostic":
            return self.write_project_predecessors_diagnostic(
                task,
                predecessor_text,
                expected_ids,
                predecessor_tasks,
            )
        return self.write_project_predecessors_fast(
            task,
            predecessor_text,
            expected_ids,
            predecessor_tasks,
        )

    def write_project_predecessors_fast(
        self,
        task: Any,
        predecessor_text: str,
        expected_ids: List[str],
        predecessor_tasks: Optional[List[Any]] = None,
    ) -> str:
        predecessor_tasks = predecessor_tasks or []
        clear_error = self.clear_project_predecessors(task) if project_task_has_predecessors(task) else ""
        if not expected_ids:
            if clear_error:
                return f"fast clear failed: {clear_error}"
            return verify_project_predecessors(task, expected_ids)

        errors: List[str] = []
        if clear_error:
            errors.append(f"fast clear failed: {clear_error}")

        predecessor_texts = unique_columns(
            [
                predecessor_text,
                ",".join(expected_ids),
                ",".join(f"{predecessor_id}FS" for predecessor_id in expected_ids),
            ]
        )
        for text in predecessor_texts:
            text_error = self.set_project_predecessor_text(task, text)
            readback_error = verify_project_predecessors(task, expected_ids)
            if not text_error and not readback_error:
                return ""
            details = []
            if text_error:
                details.append(text_error)
            if readback_error:
                details.append(readback_error)
            errors.append(f"fast text '{text}': {' | '.join(details)}")

        if predecessor_tasks:
            clear_error = self.clear_project_predecessors(task)
            if clear_error:
                errors.append(f"fast object-link clear failed: {clear_error}")
            link_error = self.link_project_predecessors(task, predecessor_tasks)
            readback_error = verify_project_predecessors(task, expected_ids)
            if not link_error and not readback_error:
                return ""
            details = []
            if link_error:
                details.append(link_error)
            if readback_error:
                details.append(readback_error)
            errors.append(f"fast Task.LinkPredecessors: {' | '.join(details)}")

        return (
            "Fast predecessor write failed. "
            + " ".join(errors)
            + " Rerun with --dependency-write-mode diagnostic for the full Microsoft Project API fallback trace."
        )

    def write_project_predecessors_diagnostic(
        self,
        task: Any,
        predecessor_text: str,
        expected_ids: List[str],
        predecessor_tasks: Optional[List[Any]] = None,
    ) -> str:
        errors: List[str] = []
        predecessor_tasks = predecessor_tasks or []
        predecessor_texts = unique_columns(
            [
                predecessor_text,
                ",".join(expected_ids),
                ",".join(f"{predecessor_id}FS" for predecessor_id in expected_ids),
            ]
        )
        unique_id_text = project_unique_id_predecessor_text(predecessor_tasks)

        if not expected_ids:
            clear_error = self.clear_project_predecessors(task)
            if clear_error:
                return f"clear failed: {clear_error}"
            return verify_project_predecessors(task, expected_ids)

        attempts: List[Tuple[str, Any]] = []
        if predecessor_tasks:
            attempts.extend(
                [
                    ("TaskDependencies.Add", lambda: self.add_project_task_dependencies(task, predecessor_tasks)),
                    ("Task.LinkPredecessors", lambda: self.link_project_predecessors(task, predecessor_tasks)),
                    (
                        "Application.LinkTasksEdit",
                        lambda: self.link_project_predecessors_by_id(task, predecessor_tasks),
                    ),
                ]
            )
        if unique_id_text:
            attempts.append(
                (
                    "Task.UniqueIDPredecessors",
                    lambda: self.set_project_unique_id_predecessors(task, unique_id_text),
                )
            )
        for text in predecessor_texts:
            attempts.append(
                (
                    f"Predecessors field '{text}'",
                    lambda value=text: self.set_project_predecessor_text(task, value),
                )
            )

        for method_name, attempt in attempts:
            clear_error = self.clear_project_predecessors(task)
            if clear_error:
                errors.append(f"{method_name}: clear failed before attempt: {clear_error}")
            attempt_error = attempt()
            readback_error = verify_project_predecessors(task, expected_ids)
            if not attempt_error and not readback_error:
                return ""
            details = []
            if attempt_error:
                details.append(attempt_error)
            if readback_error:
                details.append(readback_error)
            errors.append(f"{method_name}: {' | '.join(details)}")

        return " ".join(errors)

    def clear_project_predecessors(self, task: Any) -> str:
        errors: List[str] = []
        for predecessor_task in current_predecessor_tasks(task):
            try:
                task.UnlinkPredecessors(Tasks=predecessor_task)
            except Exception as exc:
                try:
                    task.UnlinkPredecessors(predecessor_task)
                except Exception as fallback_exc:
                    predecessor_id = safe_get(predecessor_task, "ID")
                    errors.append(
                        f"UnlinkPredecessors failed for predecessor ID {predecessor_id}: "
                        f"{exc}; fallback: {fallback_exc}"
                    )
        text_error = self.set_project_predecessor_text(task, "")
        if text_error:
            errors.append(text_error)
        return " | ".join(errors)

    def add_project_task_dependencies(self, task: Any, predecessor_tasks: List[Any]) -> str:
        errors: List[str] = []
        dependencies = safe_get(task, "TaskDependencies")
        if not dependencies:
            return "TaskDependencies collection was unavailable."
        for predecessor_task in predecessor_tasks:
            predecessor_id = safe_get(predecessor_task, "ID")
            try:
                dependencies.Add(predecessor_task)
                continue
            except Exception as exc:
                first_error = exc
            try:
                dependencies.Add(predecessor_task, 0)
                continue
            except Exception as exc:
                errors.append(
                    f"TaskDependencies.Add rejected predecessor task ID {predecessor_id}: "
                    f"{first_error}; fallback: {exc}"
                )
        return " | ".join(errors)

    def set_project_predecessor_text(self, task: Any, predecessor_text: str) -> str:
        errors = []
        try:
            task.Predecessors = predecessor_text
            return ""
        except Exception as exc:
            errors.append(f"Task.Predecessors rejected '{predecessor_text}': {exc}")

        try:
            field_id = self.app.FieldNameToFieldConstant("Predecessors")
            task.SetField(field_id, predecessor_text)
            return ""
        except Exception as exc:
            errors.append(f"Task.SetField rejected '{predecessor_text}': {exc}")

        task_id = safe_int(safe_get(task, "ID"))
        if task_id > 0:
            try:
                self.app.SetTaskField(
                    Field="Predecessors",
                    Value=predecessor_text,
                    TaskID=task_id,
                    Create=False,
                )
                return ""
            except Exception as exc:
                errors.append(f"Application.SetTaskField rejected '{predecessor_text}': {exc}")
            try:
                self.app.SetTaskField("Predecessors", predecessor_text, False, False, task_id)
                return ""
            except Exception as exc:
                errors.append(f"Application.SetTaskField positional rejected '{predecessor_text}': {exc}")
        return " | ".join(errors)

    def link_project_predecessors(self, task: Any, predecessor_tasks: List[Any]) -> str:
        errors: List[str] = []
        for predecessor_task in predecessor_tasks:
            predecessor_id = safe_get(predecessor_task, "ID")
            try:
                task.LinkPredecessors(Tasks=predecessor_task)
                continue
            except Exception as exc:
                keyword_error = exc
            try:
                task.LinkPredecessors(predecessor_task)
                continue
            except Exception as exc:
                errors.append(
                    f"LinkPredecessors rejected predecessor task ID {predecessor_id}: "
                    f"{keyword_error}; fallback: {exc}"
                )
        return " | ".join(errors)

    def link_project_predecessors_by_id(self, task: Any, predecessor_tasks: List[Any]) -> str:
        task_id = safe_int(safe_get(task, "ID"))
        if task_id <= 0:
            return "Task has no positive Project ID for Application.LinkTasksEdit."
        errors: List[str] = []
        for predecessor_task in predecessor_tasks:
            predecessor_id = safe_int(safe_get(predecessor_task, "ID"))
            if predecessor_id <= 0:
                errors.append("Predecessor task has no positive Project ID for Application.LinkTasksEdit.")
                continue
            try:
                result = self.app.LinkTasksEdit(From=predecessor_id, To=task_id, Delete=False)
                if not project_call_failed(result):
                    continue
                errors.append(
                    f"Application.LinkTasksEdit returned False for predecessor ID {predecessor_id} "
                    f"and task ID {task_id}."
                )
                continue
            except Exception as exc:
                keyword_error = exc
            try:
                result = self.app.LinkTasksEdit(predecessor_id, task_id, False)
                if not project_call_failed(result):
                    continue
                errors.append(
                    f"Application.LinkTasksEdit positional returned False for predecessor ID {predecessor_id} "
                    f"and task ID {task_id}."
                )
            except Exception as exc:
                errors.append(
                    f"Application.LinkTasksEdit rejected predecessor ID {predecessor_id} and task ID {task_id}: "
                    f"{keyword_error}; fallback: {exc}"
                )
        return " | ".join(errors)

    def set_project_unique_id_predecessors(self, task: Any, unique_id_text: str) -> str:
        try:
            task.UniqueIDPredecessors = unique_id_text
            return ""
        except Exception as exc:
            return f"Task.UniqueIDPredecessors rejected '{unique_id_text}': {exc}"

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
        project_progress("Reading post-update Project task state")
        after = self.snapshot_tasks(config)
        changed_finishes: Dict[str, Tuple[str, str]] = {}
        project_progress("Comparing Project finish dates for schedule review")
        for key, epic in plan.epics.items():
            if not epic.drives_schedule:
                continue
            before_finish = before.get(key).finish if key in before else ""
            after_finish = after.get(key).finish if key in after else ""
            if before_finish and after_finish and before_finish != after_finish:
                changed_finishes[key] = (before_finish, after_finish)
            if epic.target_end and after_finish and after_finish != epic.target_end:
                plan.audit_items.append(
                    AuditItem(
                        "Review",
                        "ScheduledDateMismatch",
                        jira_key=epic.jira_key or key,
                        schedule_key=key,
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
            project_progress("Schedule review found no Project finish-date shifts")
            return
        project_progress(f"Schedule review found {len(changed_finishes)} Project finish-date shift(s)")
        cascade_driver_keys = cascade_branch_driver_keys(plan, set(changed_finishes))
        if cascade_driver_keys:
            project_progress(
                f"Schedule review found {len(cascade_driver_keys)} branch driver finish-date shift(s)"
            )
        for key, (old_finish, new_finish) in sorted(
            changed_finishes.items(),
            key=lambda item: (item[0] not in cascade_driver_keys, item[1][1], item[0]),
        ):
            epic = plan.epics.get(key)
            is_driver = key in cascade_driver_keys
            plan.audit_items.append(
                AuditItem(
                    "Review" if is_driver else "Info",
                    "CascadeBranchDriver" if is_driver else "CascadingDateChange",
                    jira_key=epic.jira_key if epic and epic.jira_key else key,
                    schedule_key=key,
                    issue_type="Epic",
                    summary=epic.summary if epic else "",
                    field="Finish",
                    old_value=old_finish,
                    new_value=new_finish,
                    color="cascade_root" if is_driver else "changed_cell",
                    message=(
                        "Finish date changed and at least one downstream successor also shifted after auto-scheduling."
                        if is_driver
                        else "Finish date changed after auto-scheduling."
                    ),
                    reviewer_action=(
                        "Review this red finish date as a schedule branch driver before downstream changes."
                        if is_driver
                        else "Review as a downstream or independent schedule change."
                    ),
                )
            )

    def apply_review_formatting(self, plan: RunPlan, config: Dict[str, Any]) -> None:
        project_progress("Indexing Project tasks for review formatting")
        task_by_key = self.index_tasks_by_key(config)
        formatting_items: List[Tuple[AuditItem, Any, str, str, List[str]]] = []
        for item in list(plan.audit_items):
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
            column_aliases = self.project_selection_aliases(column, config)
            formatting_items.append((item, task, column, color, column_aliases))

        review_columns = review_table_columns(
            config,
            [column for _item, _task, column, _color, _aliases in formatting_items],
        )
        visible_formatting_items = [
            formatting_item
            for formatting_item in formatting_items
            if self.project_column_is_visible_for_review(formatting_item[4], review_columns, config)
        ]
        hidden_formatting_count = len(formatting_items) - len(visible_formatting_items)
        project_progress(f"Preparing to color {len(visible_formatting_items)} visible Project review cell(s)")
        if hidden_formatting_count:
            project_progress(
                f"Skipping {hidden_formatting_count} color candidate(s) because their columns are hidden by review_table.exposed_columns"
            )
        project_progress("Preparing j2p Review table")
        table_errors = self.prepare_formatting_view(review_columns, config)
        if table_errors:
            plan.audit_items.append(
                AuditItem(
                    "Warning",
                    "ProjectReviewTableSetupFailed",
                    field="Project Review Table",
                    color="review_needed",
                    message=(
                        f"Could not fully prepare the {J2P_REVIEW_TABLE_NAME} table before coloring. "
                        f"Details: {' | '.join(table_errors[:5])}"
                    ),
                    reviewer_action=(
                        "Open the sandbox in Project and confirm the j2p review columns are visible. "
                        "Then compare the manager report and audit CSV against the sandbox."
                    ),
                )
            )

        column_positions = self.project_table_column_positions(J2P_REVIEW_TABLE_NAME, config)
        failed_columns: Dict[str, int] = {}
        failed_examples: List[str] = []
        visible_total = len(visible_formatting_items)
        if visible_total:
            project_progress("Project cell coloring started")
        for index, (item, task, column, color, column_aliases) in enumerate(visible_formatting_items, start=1):
            if index == 1 or index % 50 == 0 or index == visible_total:
                project_progress(f"Project cell coloring progress: {index}/{visible_total} cell(s)")
            column_position = first_project_column_position(column_positions, column_aliases)
            error = self.color_project_cell_error(task, column, color, column_aliases, column_position)
            if not error:
                continue
            failed_columns[column] = failed_columns.get(column, 0) + 1
            if len(failed_examples) < 10:
                failed_examples.append(f"{item.jira_key or item.schedule_key} {item.field}: {error}")
        if failed_columns:
            failure_total = sum(failed_columns.values())
            columns = ", ".join(f"{column} ({count})" for column, count in sorted(failed_columns.items()))
            examples = " Examples: " + " | ".join(failed_examples) if failed_examples else ""
            plan.audit_items.append(
                AuditItem(
                    "Warning",
                    "ProjectCellColoringFailed",
                    field="Project Cell Formatting",
                    color="review_needed",
                    message=(
                        f"Could not color {failure_total} Project cell(s). Failed Project columns: {columns}. "
                        "The underlying task data was still written where Project accepted the field values."
                        f"{examples}"
                    ),
                    reviewer_action=(
                        "Review the manager report and audit CSV for changed fields. If colored cells are required, "
                        f"open the sandbox in Project and confirm the {J2P_REVIEW_TABLE_NAME} table is available "
                        "and the review columns are visible."
                    ),
                )
            )
        if visible_total:
            project_progress("Project cell coloring complete")

    def project_column_is_visible_for_review(
        self,
        column_aliases: List[str],
        review_columns: List[str],
        config: Dict[str, Any],
    ) -> bool:
        visible_aliases = {
            normalize_project_column_name(alias)
            for column in review_columns
            for alias in self.project_selection_aliases(column, config)
        }
        return any(normalize_project_column_name(alias) in visible_aliases for alias in column_aliases)

    def prepare_formatting_view(self, columns: List[str], config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        self.apply_gantt_chart_view()
        try:
            self.app.FilterClear()
        except Exception:
            pass
        try:
            self.app.GroupApply(Name="No Group")
        except Exception:
            pass
        try:
            self.app.OutlineShowAllTasks()
        except Exception:
            pass
        errors.extend(self.create_review_table(columns, config))
        table_apply_error = self.apply_project_table(J2P_REVIEW_TABLE_NAME)
        if table_apply_error:
            errors.append(table_apply_error)
        missing_columns = self.missing_review_table_columns(columns, config)
        if missing_columns:
            errors.append(
                f"{J2P_REVIEW_TABLE_NAME} is missing expected review column(s) after setup: "
                f"{', '.join(missing_columns[:20])}."
            )
        return errors

    def create_review_table(self, columns: List[str], config: Dict[str, Any]) -> List[str]:
        object_model_errors = self.recreate_review_table_with_table_fields(columns, config)
        if not object_model_errors:
            return []
        table_edit_errors = self.create_review_table_with_table_edit(columns, config)
        if not table_edit_errors:
            return []
        return [
            f"TaskTables/TableFields setup failed: {' | '.join(object_model_errors[:5])}",
            f"TableEditEx fallback failed: {' | '.join(table_edit_errors[:5])}",
        ]

    def recreate_review_table_with_table_fields(self, columns: List[str], config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        project = getattr(self, "project", None) or safe_get(self.app, "ActiveProject")
        task_tables = safe_get(project, "TaskTables")
        if not task_tables:
            return ["ActiveProject.TaskTables was unavailable."]

        existing_table = self.project_task_table(J2P_REVIEW_TABLE_NAME)
        if existing_table is not None:
            self.apply_project_table("Entry", record_error=False)
            try:
                existing_table.Delete()
                project_progress(f"Deleted existing {J2P_REVIEW_TABLE_NAME} table before rebuilding it")
            except Exception as exc:
                errors.append(f"Could not delete existing {J2P_REVIEW_TABLE_NAME} table: {exc}")
                return errors

        name_field_id, name_field_error = self.project_field_constant("Name")
        if name_field_error:
            return [name_field_error]
        try:
            table = task_tables.Add(Name=J2P_REVIEW_TABLE_NAME, Field=name_field_id, Task=True)
        except Exception as exc:
            try:
                table = task_tables.Add(J2P_REVIEW_TABLE_NAME, name_field_id, True)
            except Exception as fallback_exc:
                return [f"TaskTables.Add failed: {exc}; fallback failed: {fallback_exc}"]
        try:
            table.ShowInMenu = True
        except Exception:
            pass
        try:
            table.RowHeight = 1
        except Exception:
            pass
        errors.extend(self.add_fields_to_review_table_object(table, columns, config, ["Name"]))
        return errors

    def add_fields_to_review_table_object(
        self,
        table: Any,
        columns: List[str],
        config: Dict[str, Any],
        existing_columns: List[str],
    ) -> List[str]:
        table_fields = safe_get(table, "TableFields")
        if not table_fields:
            return [f"{J2P_REVIEW_TABLE_NAME}.TableFields was unavailable."]
        errors: List[str] = []
        existing = {normalize_project_column_name(column) for column in existing_columns}
        for column in columns:
            aliases = self.project_selection_aliases(column, config)
            if any(normalize_project_column_name(alias) in existing for alias in aliases):
                continue
            title = project_column_title(column, config)
            field_errors: List[str] = []
            added = False
            for field_name in aliases:
                field_id, field_error = self.project_field_constant(field_name)
                if field_error:
                    field_errors.append(field_error)
                    continue
                try:
                    table_fields.Add(Field=field_id, Width=18, Title=title, Before=-1, AutoWrap=True)
                except Exception as exc:
                    try:
                        table_fields.Add(field_id, 0, 18, title, 0, -1, True)
                    except Exception as fallback_exc:
                        field_errors.append(
                            f"TableFields.Add rejected {field_name}: {exc}; fallback failed: {fallback_exc}"
                        )
                        continue
                added = True
                existing.update(normalize_project_column_name(alias) for alias in aliases)
                break
            if not added:
                errors.append(f"Could not add {column} to {J2P_REVIEW_TABLE_NAME}: {' | '.join(field_errors[:3])}")
        return errors

    def create_review_table_with_table_edit(self, columns: List[str], config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        created_from_entry = False
        try:
            result = self.app.TableEditEx(
                Name="Entry",
                TaskTable=True,
                NewName=J2P_REVIEW_TABLE_NAME,
                Create=True,
                OverwriteExisting=True,
                ShowInMenu=True,
                ShowAddNewColumn=True,
            )
            if project_call_failed(result):
                errors.append(f"TableEditEx returned False while creating {J2P_REVIEW_TABLE_NAME}.")
            else:
                created_from_entry = True
        except Exception as exc:
            if project_table_name_conflict_error(exc):
                project_progress(f"{J2P_REVIEW_TABLE_NAME} table already exists; updating existing table")
            else:
                errors.append(f"TableEditEx create failed: {exc}")
                return errors

        existing_columns = self.project_table_field_names(J2P_REVIEW_TABLE_NAME)
        if not existing_columns and created_from_entry:
            existing_columns = list(PROJECT_ENTRY_TABLE_COLUMNS)
        column_errors = self.add_columns_to_table(J2P_REVIEW_TABLE_NAME, columns, config, existing_columns)
        if column_errors:
            errors.extend(column_errors)
        return errors

    def apply_project_table(self, table_name: str, record_error: bool = True) -> str:
        candidate_names = unique_columns([table_name, f"&{table_name}"])
        errors: List[str] = []
        for candidate_name in candidate_names:
            try:
                result = self.app.TableApply(Name=candidate_name)
                if not project_call_failed(result):
                    return ""
                errors.append(f"TableApply returned False for {candidate_name}.")
            except Exception as exc:
                try:
                    result = self.app.TableApply(candidate_name)
                    if not project_call_failed(result):
                        return ""
                    errors.append(f"TableApply returned False for {candidate_name}.")
                except Exception as fallback_exc:
                    errors.append(f"TableApply failed for {candidate_name}: {exc}; fallback failed: {fallback_exc}")
        return " | ".join(errors) if record_error else ""

    def missing_review_table_columns(self, columns: List[str], config: Dict[str, Any]) -> List[str]:
        table = self.project_task_table(J2P_REVIEW_TABLE_NAME)
        if table is None:
            return []
        positions = self.project_table_column_positions(J2P_REVIEW_TABLE_NAME, config)
        if not positions:
            return []
        missing: List[str] = []
        for column in columns:
            aliases = self.project_selection_aliases(column, config)
            if not first_project_column_position(positions, aliases):
                missing.append(column)
        return missing

    def project_field_constant(self, field_name: str) -> Tuple[Any, str]:
        if not field_name:
            return None, "No Project field name was provided."
        try:
            return self.app.FieldNameToFieldConstant(field_name), ""
        except Exception as exc:
            return None, f"FieldNameToFieldConstant rejected {field_name}: {exc}"

    def add_columns_to_table(
        self,
        table_name: str,
        columns: List[str],
        config: Dict[str, Any],
        existing_columns: List[str],
    ) -> List[str]:
        errors: List[str] = []
        existing = {normalize_project_column_name(column) for column in existing_columns}
        columns_to_add = [
            column
            for column in columns
            if not any(
                normalize_project_column_name(alias) in existing
                for alias in self.project_selection_aliases(column, config)
            )
        ]
        for position, column in enumerate(columns_to_add, start=2):
            title = project_column_title(column, config)
            candidate_field_names = self.project_selection_aliases(column, config)
            added = False
            candidate_errors: List[str] = []
            for field_name in candidate_field_names:
                try:
                    result = self.app.TableEditEx(
                        Name=table_name,
                        TaskTable=True,
                        FieldName="",
                        NewFieldName=field_name,
                        Title=title,
                        Width=18,
                        ColumnPosition=position,
                        ShowInMenu=True,
                        HeaderTextWrap=True,
                        WrapText=True,
                        ShowAddNewColumn=True,
                    )
                    if project_call_failed(result):
                        candidate_errors.append(
                            f"TableEditEx returned False while adding {field_name} to {table_name}."
                        )
                        continue
                    added = True
                    existing.update(
                        normalize_project_column_name(alias)
                        for alias in self.project_selection_aliases(column, config)
                    )
                    break
                except Exception as exc:
                    if project_table_column_already_present_error(exc):
                        added = True
                        existing.update(
                            normalize_project_column_name(alias)
                            for alias in self.project_selection_aliases(column, config)
                        )
                        break
                    candidate_errors.append(f"TableEditEx add {field_name} to {table_name} failed: {exc}")
            if added:
                continue
            if candidate_errors:
                errors.append(" | ".join(candidate_errors))
            else:
                errors.append(f"No Project field name candidate was available for {column}.")
        return errors

    def project_selection_aliases(self, column: str, config: Dict[str, Any]) -> List[str]:
        aliases = project_column_aliases(column, config)
        friendly_name = project_column_title(column, config)
        if friendly_name:
            aliases.append(friendly_name)
        for alias in list(aliases):
            aliases.extend(self.project_resolved_field_aliases(alias))
        aliases.extend(project_native_field_aliases(column))
        return unique_columns(aliases)

    def project_resolved_field_aliases(self, field_name: str) -> List[str]:
        aliases: List[str] = []
        if not field_name:
            return aliases
        try:
            field_id = self.app.FieldNameToFieldConstant(field_name)
        except Exception:
            return aliases
        try:
            resolved = str(self.app.FieldConstantToFieldName(field_id))
            if resolved:
                aliases.append(resolved)
        except Exception:
            pass
        return aliases

    def project_table_column_positions(self, table_name: str, config: Dict[str, Any]) -> Dict[str, int]:
        table = self.project_task_table(table_name)
        if table is None:
            return {}
        table_fields = safe_get(table, "TableFields")
        if not table_fields:
            return {}
        positions: Dict[str, int] = {}
        try:
            count = int(table_fields.Count)
        except Exception:
            return positions
        for index in range(1, count + 1):
            try:
                table_field = table_fields(index)
            except Exception:
                continue
            for name in self.project_table_field_aliases(table_field, config):
                normalized = normalize_project_column_name(name)
                if normalized and normalized not in positions:
                    positions[normalized] = index
        return positions

    def project_table_field_aliases(self, table_field: Any, config: Dict[str, Any]) -> List[str]:
        aliases: List[str] = []
        title = str(safe_get(table_field, "Title") or "").strip()
        if title:
            aliases.append(title)
        field_value = safe_get(table_field, "Field")
        if field_value not in ("", None):
            if isinstance(field_value, str):
                aliases.append(field_value)
            else:
                try:
                    aliases.append(str(self.app.FieldConstantToFieldName(field_value)))
                except Exception:
                    aliases.append(str(field_value))
        for alias in list(aliases):
            aliases.extend(project_column_aliases(alias, config))
            aliases.extend(project_native_field_aliases(alias))
            aliases.extend(self.project_resolved_field_aliases(alias))
        return unique_columns(aliases)

    def project_table_field_names(self, table_name: str) -> List[str]:
        positions = self.project_table_column_positions(table_name, {})
        return unique_columns(list(positions))

    def project_task_table(self, table_name: str) -> Any:
        project = getattr(self, "project", None) or safe_get(self.app, "ActiveProject")
        task_tables = safe_get(project, "TaskTables")
        if not task_tables:
            return None
        try:
            return task_tables(table_name)
        except Exception:
            pass
        try:
            return task_tables.Item(table_name)
        except Exception:
            return None

    def color_project_cell(
        self,
        task: Any,
        column: str,
        hex_color: str,
        column_aliases: Optional[List[str]] = None,
    ) -> bool:
        return not self.color_project_cell_error(task, column, hex_color, column_aliases)

    def color_project_cell_error(
        self,
        task: Any,
        column: str,
        hex_color: str,
        column_aliases: Optional[List[str]] = None,
        column_position: Optional[int] = None,
    ) -> str:
        color = project_color(hex_color)
        selected, selection_error = self.select_project_cell(task, column_aliases or [column], column_position)
        if not selected:
            return selection_error
        return self.color_active_cell(color, hex_color)

    def select_project_cell(
        self,
        task: Any,
        columns: Any,
        column_position: Optional[int] = None,
    ) -> Tuple[bool, str]:
        row = int(safe_get(task, "ID") or 0)
        if row <= 0:
            return False, "Task has no positive Project row ID."
        if isinstance(columns, str):
            candidate_columns = [columns]
        else:
            candidate_columns = [str(column) for column in columns if str(column or "").strip()]
        errors: List[str] = []
        for candidate_position in project_selection_column_numbers(column_position):
            selected, error = self.select_project_cell_by_position(row, candidate_position, candidate_columns)
            if selected:
                return True, ""
            if error:
                errors.append(error)
        for column in unique_columns(candidate_columns):
            selected, error = self.select_project_cell_by_name(row, column, candidate_columns)
            if selected:
                return True, ""
            if error:
                errors.append(error)
        return False, " ".join(errors)

    def select_project_cell_by_position(
        self,
        row: int,
        column_position: int,
        candidate_columns: List[str],
    ) -> Tuple[bool, str]:
        errors: List[str] = []
        selectors = (
            (
                "SelectCell",
                lambda: self.app.SelectCell(Row=row, Column=column_position, RowRelative=False),
            ),
            (
                "SelectCell positional",
                lambda: self.app.SelectCell(row, column_position, False),
            ),
            (
                "SelectRange",
                lambda: self.app.SelectRange(
                    Row=row,
                    Column=column_position,
                    RowRelative=False,
                    Width=0,
                    Height=0,
                    Extend=False,
                    Add=False,
                ),
            ),
            (
                "SelectRange positional",
                lambda: self.app.SelectRange(row, column_position, False, 0, 0, False, False),
            ),
        )
        for method_name, selector in selectors:
            try:
                result = selector()
            except Exception as exc:
                errors.append(f"{method_name} failed for column position {column_position}: {exc}")
                continue
            if project_call_failed(result):
                errors.append(f"{method_name} returned False for column position {column_position}.")
                continue
            selected_field_error = self.selected_cell_field_mismatch(candidate_columns)
            if not selected_field_error:
                return True, ""
            errors.append(
                f"{method_name} selected column position {column_position}, but {selected_field_error}"
            )
        return False, " ".join(errors)

    def select_project_cell_by_name(
        self,
        row: int,
        column: str,
        candidate_columns: List[str],
    ) -> Tuple[bool, str]:
        errors: List[str] = []
        selectors = (
            (
                "SelectTaskField",
                lambda: self.app.SelectTaskField(Row=row, Column=column, RowRelative=False),
            ),
            (
                "SelectTaskField positional",
                lambda: self.app.SelectTaskField(row, column, False),
            ),
            (
                "SelectTaskField extended",
                lambda: self.app.SelectTaskField(
                    Row=row,
                    Column=column,
                    RowRelative=False,
                    Width=0,
                    Height=0,
                    Extend=False,
                    Add=False,
                ),
            ),
            (
                "SelectTaskField extended positional",
                lambda: self.app.SelectTaskField(row, column, False, 0, 0, False, False),
            ),
            (
                "SelectTaskCell",
                lambda: self.app.SelectTaskCell(Row=row, Column=column, RowRelative=False),
            ),
            (
                "SelectTaskCell positional",
                lambda: self.app.SelectTaskCell(row, column, False),
            ),
        )
        for method_name, selector in selectors:
            try:
                result = selector()
            except Exception as exc:
                errors.append(f"{method_name} failed for {column}: {exc}")
                continue
            if project_call_failed(result):
                errors.append(f"{method_name} returned False for {column}.")
                continue
            selected_field_error = self.selected_cell_field_mismatch(candidate_columns)
            if not selected_field_error:
                return True, ""
            errors.append(f"{method_name} selected {column}, but {selected_field_error}")
        return False, " ".join(errors)

    def selected_cell_field_mismatch(self, candidate_columns: List[str]) -> str:
        expected = {
            normalize_project_column_name(alias)
            for column in candidate_columns
            for alias in project_native_field_aliases(column) + [column]
        }
        try:
            active_cell = self.app.ActiveCell
        except Exception:
            return ""
        field_name = str(safe_get(active_cell, "FieldName") or "").strip()
        if not field_name:
            return ""
        if normalize_project_column_name(field_name) in expected:
            return ""
        return f"ActiveCell.FieldName was '{field_name}' instead of one of {sorted(expected)}."

    def color_active_cell(self, color: int, hex_color: str = "") -> str:
        try:
            active_cell = self.app.ActiveCell
        except Exception as exc:
            return f"ActiveCell was unavailable after selecting the Project cell: {exc}"
        exact_color_error = self.color_active_cell_with_cell_color_ex(active_cell, color)
        if not exact_color_error:
            return ""
        palette_color = project_pj_color(hex_color, color)
        try:
            active_cell = self.app.ActiveCell
        except Exception:
            pass
        palette_error = self.color_active_cell_with_cell_color(active_cell, palette_color)
        if not palette_error:
            return ""
        return f"{exact_color_error} ActiveCell.CellColor fallback failed: {palette_error}"

    def color_active_cell_with_cell_color_ex(self, active_cell: Any, color: int) -> str:
        errors: List[str] = []
        for value in project_com_int_values(color):
            try:
                active_cell.Pattern = PROJECT_SOLID_FILL_PATTERN
            except Exception:
                pass
            try:
                active_cell.CellColorEx = value
            except Exception as exc:
                errors.append(f"ActiveCell.CellColorEx rejected {project_com_value_label(value)}: {exc}")
                continue
            try:
                active_cell.Pattern = PROJECT_SOLID_FILL_PATTERN
            except Exception:
                pass
            readback_error = self.active_cell_color_ex_readback_error(color)
            if not readback_error:
                return ""
            errors.append(readback_error)
        return " ".join(errors) or "ActiveCell.CellColorEx was unavailable."

    def color_active_cell_with_cell_color(self, active_cell: Any, color: int) -> str:
        errors: List[str] = []
        for value in project_com_int_values(color):
            try:
                active_cell.Pattern = PROJECT_SOLID_FILL_PATTERN
            except Exception:
                pass
            try:
                active_cell.CellColor = value
            except Exception as exc:
                errors.append(f"ActiveCell.CellColor rejected {project_com_value_label(value)}: {exc}")
                continue
            try:
                active_cell.Pattern = PROJECT_SOLID_FILL_PATTERN
            except Exception:
                pass
            readback_error = self.active_cell_color_readback_error(color)
            if not readback_error:
                return ""
            errors.append(readback_error)
        return " ".join(errors) or "ActiveCell.CellColor was unavailable."

    def active_cell_color_ex_readback_error(self, color: int) -> str:
        try:
            active_cell = self.app.ActiveCell
        except Exception:
            return ""
        try:
            readback = int(active_cell.CellColorEx)
            if readback != color:
                return f"ActiveCell.CellColorEx read back {readback}, expected {color}."
        except Exception:
            pass
        return ""

    def active_cell_color_readback_error(self, color: int) -> str:
        try:
            active_cell = self.app.ActiveCell
        except Exception:
            return ""
        try:
            readback = int(active_cell.CellColor)
            if readback != color:
                return f"ActiveCell.CellColor read back {readback}, expected {color}."
        except Exception:
            pass
        return ""

def project_column_for_audit_field(field_name: str, config: Dict[str, Any]) -> str:
    fields = config.get("project_fields", {})
    story_point_ratio_field = story_point_ratio_project_field(config)
    story_point_ratio_name = str(
        config.get("project_field_names", {}).get("story_point_ratio", "Story Point Ratio")
    )
    mapping = {
        "Name": "Name",
        "% Complete": "% Complete",
        "Predecessors": "Predecessors",
        "Successors": "Successors",
        "Finish": "Finish",
        "Start": "Start",
        "Status": fields.get("jira_status", "Text9"),
        "Resource Group": "Resource Group",
        "Rollup": fields.get("rollup_key", "Text5"),
        "Rollup Key": fields.get("rollup_key", "Text5"),
        "Schedule Key": fields.get("j2p_key", "Text10"),
        "Row Role": fields.get("row_role", "Text11"),
        "Fix Version": fields.get("fix_version", "Text12"),
        "Fix versions": fields.get("fix_version", "Text12"),
        "Drives Schedule": fields.get("drives_schedule", "Flag4"),
        "Primary Schedule Key": fields.get("primary_schedule_key", "Text13"),
        "Total Story Points": fields.get("total_story_points", "Number1"),
        "Completed Story Points": fields.get("completed_story_points", "Number2"),
        "Logged Hours": fields.get("logged_hours", "Number3"),
        story_point_ratio_name: story_point_ratio_field,
        "Story Point Ratio": story_point_ratio_field,
        "In Planning": fields.get("in_planning", "Flag1"),
        "Unmatched Project Task": fields.get("unmatched_project_task", "Flag2"),
        "Dependency Review": fields.get("dependency_review", "Text8"),
        "Jira Target Start": fields.get("jira_target_start", "Date1"),
        "Jira Target End": fields.get("jira_target_end", "Date2"),
    }
    return mapping.get(field_name, "")


def review_table_columns(config: Dict[str, Any], audit_columns: List[str]) -> List[str]:
    standard_columns = review_table_standard_columns(config)
    visible_columns = exposed_review_table_columns(config, standard_columns)
    if config.get("review_table", {}).get("include_audit_columns", True):
        visible_columns.extend(audit_columns)
    return unique_columns(visible_columns)


def review_table_standard_columns(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    fields = config.get("project_fields", {})
    return [
        ("name", "Name"),
        ("jira_key", fields.get("jira_key", "Text1")),
        ("j2p_key", fields.get("j2p_key", "Text10")),
        ("jira_issue_type", fields.get("jira_issue_type", "Text3")),
        ("rollup_mode", fields.get("rollup_mode", "Text4")),
        ("rollup_key", fields.get("rollup_key", "Text5")),
        ("jira_key_prefix", fields.get("jira_key_prefix", "Text7")),
        ("resource_group", "Resource Group"),
        ("dependency_review", fields.get("dependency_review", "Text8")),
        ("jira_status", fields.get("jira_status", "Text9")),
        ("start", "Start"),
        ("finish", "Finish"),
        ("jira_target_start", fields.get("jira_target_start", "Date1")),
        ("jira_target_end", fields.get("jira_target_end", "Date2")),
        ("percent_complete", "% Complete"),
        ("total_story_points", fields.get("total_story_points", "Number1")),
        ("completed_story_points", fields.get("completed_story_points", "Number2")),
        ("logged_hours", fields.get("logged_hours", "Number3")),
        ("story_point_ratio", story_point_ratio_project_field(config)),
        ("in_planning", fields.get("in_planning", "Flag1")),
        ("unmatched_project_task", fields.get("unmatched_project_task", "Flag2")),
        ("dependency_review_needed", fields.get("dependency_review_needed", "Flag3")),
        ("row_role", fields.get("row_role", "Text11")),
        ("fix_version", fields.get("fix_version", "Text12")),
        ("drives_schedule", fields.get("drives_schedule", "Flag4")),
        ("primary_schedule_key", fields.get("primary_schedule_key", "Text13")),
        ("predecessors", "Predecessors"),
    ]


def exposed_review_table_columns(
    config: Dict[str, Any],
    standard_columns: List[Tuple[str, str]],
) -> List[str]:
    exposed_columns = config.get("review_table", {}).get("exposed_columns", "all")
    if exposed_columns == "all":
        return [column for _key, column in standard_columns]
    requested_columns = [str(column) for column in exposed_columns]
    if any(review_table_column_key(column) == "all" for column in requested_columns):
        return [column for _key, column in standard_columns]
    lookup = review_table_column_lookup(config, standard_columns)
    selected = ["Name"]
    for requested_column in requested_columns:
        column = lookup.get(review_table_column_key(requested_column), requested_column.strip())
        selected.append(column)
    return selected


def review_table_column_lookup(
    config: Dict[str, Any],
    standard_columns: List[Tuple[str, str]],
) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    alias_overrides = {
        "summary": "name",
        "task_name": "name",
        "jira_key_prefix": "jira_key_prefix",
        "key_prefix": "jira_key_prefix",
        "prefix": "jira_key_prefix",
        "resource_names": "resource_group",
        "status": "jira_status",
        "percent_complete": "percent_complete",
        "complete": "percent_complete",
        "completion": "percent_complete",
        "target_start": "jira_target_start",
        "target_end": "jira_target_end",
        "story_points": "total_story_points",
        "points": "total_story_points",
        "completed_points": "completed_story_points",
        "ratio": "story_point_ratio",
        "story_point_ratio": "story_point_ratio",
        "dependency": "dependency_review",
        "dependency_needed": "dependency_review_needed",
    }
    standard_by_key = {key: column for key, column in standard_columns}
    for key, column in standard_columns:
        aliases = unique_columns(
            [
                key,
                column,
                project_column_title(column, config),
                *project_native_field_aliases(column),
            ]
        )
        for alias in aliases:
            lookup[review_table_column_key(alias)] = column
    for alias, key in alias_overrides.items():
        column = standard_by_key.get(key)
        if column:
            lookup[alias] = column
    return lookup


def review_table_column_key(column: str) -> str:
    text = normalize_project_column_name(column)
    text = text.replace("%", "percent")
    cleaned = []
    previous_separator = False
    for character in text:
        if character.isalnum():
            cleaned.append(character)
            previous_separator = False
        elif not previous_separator:
            cleaned.append("_")
            previous_separator = True
    return "".join(cleaned).strip("_")


def unique_columns(columns: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for column in columns:
        column_text = str(column or "").strip()
        key = column_text.lower()
        if not column_text or key in seen:
            continue
        seen.add(key)
        result.append(column_text)
    return result


def project_column_title(column: str, config: Dict[str, Any]) -> str:
    for logical_name, project_field in config.get("project_fields", {}).items():
        if project_field == column:
            return str(config.get("project_field_names", {}).get(logical_name) or column)
    titles = {
        "% Complete": "% Complete",
        "Finish": "Finish",
        "Name": "Name",
        "Predecessors": "Predecessors",
        "Resource Group": "Resource Group",
        "Start": "Start",
    }
    return titles.get(column, column)


def story_point_ratio_project_field(config: Dict[str, Any]) -> str:
    fields = config.get("project_fields", {})
    return str(fields.get("story_point_ratio", "Number4"))


def project_column_aliases(column: str, config: Dict[str, Any]) -> List[str]:
    title = project_column_title(column, config)
    return unique_columns([column, title])


def project_native_field_aliases(column: str) -> List[str]:
    aliases = {
        "% complete": ["% Complete", "Percent Complete"],
        "percent complete": ["% Complete", "Percent Complete"],
        "resource group": ["Resource Group", "Resource Names"],
        "resource names": ["Resource Names", "Resource Group"],
        "start": ["Start"],
        "finish": ["Finish"],
        "name": ["Name"],
        "predecessors": ["Predecessors"],
        "successors": ["Successors"],
    }
    return aliases.get(normalize_project_column_name(column), [])


def first_project_column_position(column_positions: Dict[str, int], candidate_columns: List[str]) -> Optional[int]:
    for column in candidate_columns:
        position = column_positions.get(normalize_project_column_name(column))
        if position:
            return position
    return None


def project_selection_column_numbers(column_position: Optional[int]) -> List[int]:
    if not column_position or column_position <= 0:
        return []
    return [column_position, column_position + 1]


def normalize_project_column_name(column: str) -> str:
    return str(column or "").strip().lower()


def project_table_name_conflict_error(error: Any) -> bool:
    text = str(error).lower()
    return "already" in text and ("used" in text or "exist" in text)


def project_table_column_already_present_error(error: Any) -> bool:
    text = str(error).lower()
    return "already" in text and ("column" in text or "field" in text or "table" in text)


def project_call_failed(result: Any) -> bool:
    return result is False or result == 0


def safe_get(task: Any, name: str) -> Any:
    try:
        return getattr(task, name)
    except Exception:
        return ""


def safe_set(task: Any, name: str, value: Any) -> bool:
    try:
        setattr(task, name, value)
        return True
    except Exception:
        return False


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


def project_date_for_com(value: str, schedule_attribute: str = "") -> Any:
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Date '{value}' must be a valid YYYY-MM-DD date before writing to Project.") from exc
    if parsed.year < 1984 or parsed.year > 2149:
        raise ValueError(
            f"Date '{value}' is outside the Microsoft Project supported range of 1984-01-01 through 2149-12-31."
        )
    if schedule_attribute == "Finish":
        parsed = parsed.replace(hour=17, minute=0, second=0)
    else:
        parsed = parsed.replace(hour=8, minute=0, second=0)
    try:
        import pywintypes  # type: ignore

        return pywintypes.Time(parsed)
    except Exception:
        return parsed


def parse_project_key_list(value: str) -> List[str]:
    # Project predecessor strings are often row IDs rather than Jira keys. Keep
    # only Jira-looking tokens when custom views include them.
    import re

    return sorted(set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", value.upper())))


def project_predecessor_ids(value: str) -> List[str]:
    import re

    return re.findall(r"\b(\d+)(?:[A-Z]{0,2})?(?:[+-]\d+[a-zA-Z]+)?\b", value)


def project_task_has_predecessors(task: Any) -> bool:
    if task is None:
        return False
    return bool(project_predecessor_ids(str(safe_get(task, "Predecessors"))) or current_predecessor_task_ids(task))


def current_predecessor_tasks(task: Any) -> List[Any]:
    result: List[Any] = []
    collection = safe_get(task, "PredecessorTasks")
    if not collection:
        return result
    try:
        count = int(collection.Count)
    except Exception:
        return result
    for index in range(1, count + 1):
        try:
            predecessor_task = collection(index)
        except Exception:
            continue
        if predecessor_task is not None:
            result.append(predecessor_task)
    return result


def current_predecessor_task_ids(task: Any) -> List[str]:
    ids: List[str] = []
    for predecessor_task in current_predecessor_tasks(task):
        predecessor_id = safe_int(safe_get(predecessor_task, "ID"))
        if predecessor_id > 0:
            ids.append(str(predecessor_id))
    return ids


def project_unique_id_predecessor_text(predecessor_tasks: List[Any]) -> str:
    unique_ids: List[str] = []
    for predecessor_task in predecessor_tasks:
        unique_id = safe_int(safe_get(predecessor_task, "UniqueID"))
        if unique_id > 0:
            unique_ids.append(str(unique_id))
    return ",".join(unique_ids)


def verify_project_predecessors(task: Any, expected_ids: List[str]) -> str:
    current_value = str(safe_get(task, "Predecessors"))
    current_ids = unique_columns(project_predecessor_ids(current_value) + current_predecessor_task_ids(task))
    missing_ids = [predecessor_id for predecessor_id in expected_ids if predecessor_id not in current_ids]
    unexpected_ids = [predecessor_id for predecessor_id in current_ids if predecessor_id not in expected_ids]
    if missing_ids:
        return (
            f"Microsoft Project did not retain predecessor ID(s) {', '.join(missing_ids)}. "
            f"Current Project value: '{current_value}'."
        )
    if unexpected_ids:
        return (
            f"Microsoft Project retained unexpected predecessor ID(s) {', '.join(unexpected_ids)}. "
            f"Current Project value: '{current_value}'."
        )
    return ""


def append_resource_name(current: str, resource_name: str) -> str:
    names = [name.strip() for name in current.split(",") if name.strip()]
    if resource_name not in names:
        names.append(resource_name)
    return ", ".join(names)


def project_color(hex_color: str) -> int:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        return -16777216
    red = int(cleaned[0:2], 16)
    green = int(cleaned[2:4], 16)
    blue = int(cleaned[4:6], 16)
    return (blue << 16) + (green << 8) + red


def project_pj_color(hex_color: str, project_rgb_color: Optional[int] = None) -> int:
    cleaned = str(hex_color or "").strip().lower()
    default_color_map = {
        "#c6efce": PJ_COLOR_LIME,
        "c6efce": PJ_COLOR_LIME,
        "#ffc7ce": PJ_COLOR_RED,
        "ffc7ce": PJ_COLOR_RED,
        "#ffeb9c": PJ_COLOR_YELLOW,
        "ffeb9c": PJ_COLOR_YELLOW,
        "#bdd7ee": PJ_COLOR_BLUE,
        "bdd7ee": PJ_COLOR_BLUE,
        "#d9ead3": PJ_COLOR_SILVER,
        "d9ead3": PJ_COLOR_SILVER,
    }
    if cleaned in default_color_map:
        return default_color_map[cleaned]
    red, green, blue = color_components(hex_color, project_rgb_color)
    hue, saturation, lightness = rgb_to_hsl(red, green, blue)
    if saturation < 0.12:
        return PJ_COLOR_SILVER if lightness > 0.55 else PJ_COLOR_GRAY
    if hue < 20 or hue >= 340:
        return PJ_COLOR_RED
    if hue < 70:
        return PJ_COLOR_YELLOW
    if hue < 160:
        return PJ_COLOR_LIME
    if hue < 200:
        return PJ_COLOR_AQUA
    if hue < 260:
        return PJ_COLOR_BLUE
    return PJ_COLOR_RED if hue >= 330 else PJ_COLOR_BLUE


def color_components(hex_color: str, project_rgb_color: Optional[int] = None) -> Tuple[int, int, int]:
    cleaned = str(hex_color or "").strip().lstrip("#")
    if len(cleaned) == 6:
        try:
            return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)
        except ValueError:
            pass
    value = int(project_rgb_color or 0)
    red = value & 0xFF
    green = (value >> 8) & 0xFF
    blue = (value >> 16) & 0xFF
    return red, green, blue


def rgb_to_hsl(red: int, green: int, blue: int) -> Tuple[float, float, float]:
    red_f = red / 255.0
    green_f = green / 255.0
    blue_f = blue / 255.0
    max_value = max(red_f, green_f, blue_f)
    min_value = min(red_f, green_f, blue_f)
    lightness = (max_value + min_value) / 2.0
    if max_value == min_value:
        return 0.0, 0.0, lightness
    delta = max_value - min_value
    saturation = delta / (2.0 - max_value - min_value) if lightness > 0.5 else delta / (max_value + min_value)
    if max_value == red_f:
        hue = ((green_f - blue_f) / delta + (6 if green_f < blue_f else 0)) * 60.0
    elif max_value == green_f:
        hue = ((blue_f - red_f) / delta + 2) * 60.0
    else:
        hue = ((red_f - green_f) / delta + 4) * 60.0
    return hue, saturation, lightness


def project_com_int_values(value: int) -> List[Any]:
    values: List[Any] = [int(value)]
    try:
        import pythoncom  # type: ignore
        from win32com.client import VARIANT  # type: ignore

        values.append(VARIANT(pythoncom.VT_I4, int(value)))
    except Exception:
        pass
    return values


def project_com_value_label(value: Any) -> str:
    try:
        return str(int(value))
    except Exception:
        return type(value).__name__
