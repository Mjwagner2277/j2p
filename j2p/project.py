"""Microsoft Project automation adapter.

This module is imported on every platform, but it only imports pywin32 when a
command needs to open or write an MPP file.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import AuditItem, PlanEpic, ProjectTaskSnapshot, RunPlan
from .project_values import epic_assignments, epic_context, value_metadata
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
        session.verify_saved_plan(plan, config)
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
        session.verify_saved_plan(plan, config)
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
        self.project_path: Optional[Path] = None
        self.resource_assignment_warnings: List[str] = []

    def __enter__(self) -> "MicrosoftProjectSession":
        try:
            self.pythoncom.CoInitialize()
            self.com_initialized = True
        except Exception:
            pass
        try:
            self.app = self.create_application()
            self.previous_window_state = {
                name: safe_get(self.app, name) for name in ("Visible", "DisplayAlerts")
            }
            self.configure_application_window()
            return self
        except Exception:
            if self.com_initialized:
                self.pythoncom.CoUninitialize()
                self.com_initialized = False
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self.project is not None:
                try:
                    self.close_project(save_changes=False)
                except Exception as cleanup_error:
                    if exc_type is None:
                        raise
                    project_progress(f"Project cleanup also failed: {cleanup_error}")
        finally:
            if self.app is not None and not self.owns_app:
                for name, value in getattr(self, "previous_window_state", {}).items():
                    if value != "":
                        safe_set(self.app, name, value)
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
            if getattr(self, "owns_app", True) or self.visible:
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
            result = self.app.FileOpen(Name=str(path))
        except Exception:
            result = self.app.FileOpen(str(path))
        require_project_command(result, "open sandbox")
        self.project = self.app.ActiveProject
        self.project_path = path
        self.saved_successfully = False
        self.assert_project_identity()
        self.apply_gantt_chart_view()
        project_progress("Project file opened")

    def new(self) -> None:
        self.project_path = None
        self.project = self.create_blank_project()
        if self.project is None:
            self.project = safe_get(self.app, "ActiveProject")
        if not self.project:
            raise ProjectAutomationError(
                "Microsoft Project did not return an active blank project after creating a new file. "
                f"{PROJECT_TASK_MANAGER_RESOLUTION}"
            )
        self.unsaved_project_name = str(safe_get(self.project, "Name"))
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
                    require_project_command(project, "create blank project")
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
                require_project_command(create_project(), "create blank project")
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

    def assert_project_identity(self, expected_path: Optional[Path] = None) -> None:
        """Never write or save a different project that became active in the UI."""
        expected = expected_path or getattr(self, "project_path", None)
        if expected is None:
            # Unsaved projects do not have a filesystem identity yet. Their name must
            # remain the one returned by new(), including when using a borrowed app.
            if hasattr(self, "unsaved_project_name"):
                actual_name = str(safe_get(safe_get(self.app, "ActiveProject"), "Name"))
                if not self.unsaved_project_name or actual_name != self.unsaved_project_name:
                    raise ProjectAutomationError("The active unsaved Project changed before Save As.")
            return
        try:
            actual = Path(str(self.app.ActiveProject.FullName)).expanduser().resolve()
            bound = Path(str(self.project.FullName)).expanduser().resolve()
        except Exception as exc:
            raise ProjectAutomationError("Could not verify the active Microsoft Project file identity.") from exc
        if actual != expected.resolve() or bound != expected.resolve():
            raise ProjectAutomationError(
                f"Active Microsoft Project file does not match the sandbox. Expected: {expected}; "
                f"active: {actual}; bound: {bound}. No further writes are allowed."
            )

    def save(self) -> None:
        self.assert_project_identity()
        self.saved_successfully = False
        require_project_command(self.app.FileSave(), "save sandbox")
        self.assert_project_identity()
        self.require_saved_file()
        self.saved_successfully = True
        project_progress("Project save complete")

    def save_as(self, path: Path) -> None:
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.assert_project_identity()
        self.saved_successfully = False
        try:
            result = self.app.FileSaveAs(Name=str(path))
        except Exception:
            result = self.app.FileSaveAs(str(path))
        require_project_command(result, "save sandbox as a new file")
        self.project = self.app.ActiveProject
        self.project_path = path
        self.assert_project_identity()
        self.require_saved_file()
        self.saved_successfully = True
        project_progress("Project Save As complete")

    def require_saved_file(self) -> None:
        path = getattr(self, "project_path", None)
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise ProjectAutomationError(f"Microsoft Project did not produce a nonempty sandbox file: {path}")

    def close_project(self, save_changes: bool) -> None:
        self.assert_project_identity()
        save_option = 1 if save_changes else 0
        errors = []
        for close in (
            lambda: self.app.FileCloseEx(Save=save_option, NoAuto=True, CheckIn=False),
            lambda: self.app.FileCloseEx(save_option, True, False),
            lambda: self.app.FileClose(Save=save_option),
        ):
            try:
                self.assert_project_identity()
                require_project_command(close(), "close sandbox with an explicit save policy")
                active = safe_get(self.app, "ActiveProject")
                expected_path = getattr(self, "project_path", None)
                current_path = str(safe_get(active, "FullName")) if active else ""
                if expected_path is not None and current_path and Path(current_path).resolve() == expected_path.resolve():
                    raise ProjectAutomationError("Project reported close success but the sandbox is still active.")
                if expected_path is None and getattr(self, "unsaved_project_name", ""):
                    if str(safe_get(active, "Name")) == self.unsaved_project_name:
                        raise ProjectAutomationError("Project reported close success but the blank project is still active.")
                return
            except Exception as exc:
                errors.append(str(exc))
        raise ProjectAutomationError("Could not close the sandbox safely: " + " | ".join(errors))

    def recalculate(self) -> None:
        self.assert_project_identity()
        project_progress("Project recalculation started")
        errors = []
        for calculate in (lambda: self.app.CalculateProject(), lambda: self.app.CalculateAll()):
            try:
                require_project_command(calculate(), "recalculate sandbox")
                project_progress("Project recalculation complete")
                return
            except Exception as exc:
                errors.append(str(exc))
        raise ProjectAutomationError("Microsoft Project could not recalculate the sandbox: " + " | ".join(errors))

    def verify_saved_plan(self, plan: RunPlan, config: Dict[str, Any]) -> None:
        """Require the persisted file to retain the verified plan before reporting success."""
        self.saved_successfully = False
        plan.stats.pop("project_verification", None)
        started = time.monotonic()
        project_progress("Starting saved sandbox verification before close/reopen")
        self.require_saved_file()
        self.verify_plan(plan, config)
        project_progress("Reading task snapshot before closing the saved sandbox")
        before = {key: asdict(value) for key, value in self.snapshot_tasks(
            config, progress_label="Before-close snapshot"
        ).items()}
        path = self.project_path
        project_progress("Closing the saved sandbox for verification")
        self.close_project(save_changes=False)
        self.project = None
        project_progress("Hashing the closed sandbox file")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        persisted_hash = digest.hexdigest()
        project_progress("Reopening the saved sandbox for verification")
        self.open(path)
        fields_verified = self.verify_plan(plan, config)
        project_progress("Reading task snapshot after reopening the saved sandbox")
        after = {key: asdict(value) for key, value in self.snapshot_tasks(
            config, progress_label="After-reopen snapshot"
        ).items()}
        project_progress("Comparing saved sandbox snapshots")
        if before != after:
            changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
            raise ProjectAutomationError(
                "Saved sandbox readback changed task state for: " + ", ".join(changed[:20])
            )
        self.saved_successfully = True
        plan.stats["project_verification"] = {
            "project_version": str(safe_get(self.app, "Version")),
            "path": str(path),
            "save_reopen": True,
            "verified_epics": len(plan.epics),
            "verified_fields": fields_verified,
            "sha256": persisted_hash,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        project_progress(f"Saved sandbox verification passed in {time.monotonic() - started:.1f}s")

    def verify_plan(self, plan: RunPlan, config: Dict[str, Any]) -> int:
        project_progress("Indexing Project tasks for verification")
        self.assert_project_identity()
        task_list = self.iter_tasks(progress_label="Verification task scan")
        tasks = self.index_tasks_by_key(config, task_list)
        summaries = self.index_rollup_summaries(config, task_list)
        project_progress("Indexing Project resources for verification")
        resources = {int(resource.ID): resource for resource in self.iter_resources()} if plan.epics else {}
        count = 0
        total_epics = len(plan.epics)
        project_progress(f"Verifying {total_epics} epic row(s), including percent complete and dependencies")
        for index, epic in enumerate(plan.epics.values(), start=1):
            if index == 1 or index % 50 == 0 or index == total_epics:
                project_progress(f"Epic verification progress: {index}/{total_epics} row(s), epic={epic.key}")
            task = tasks.get(epic.key.upper())
            if task is None:
                raise ProjectAutomationError(f"Saved sandbox is missing epic {epic.key}.")
            context = epic_context(epic)
            for field, value in epic_assignments(epic, config):
                verify_project_value(task, field, value, context)
                count += 1
            for logical, date_text in (("jira_target_start", epic.target_start), ("jira_target_end", epic.target_end)):
                if date_text:
                    field = config["project_fields"][logical]
                    if project_date_to_iso(safe_get(task, field)) != date_text:
                        raise ProjectAutomationError(f"Jira date readback failed: {context}, field={field}.")
                    count += 1
            verify_project_value(task, "Manual", False, context)
            verify_project_value(task, "Active", not epic.completed and epic.drives_schedule, context)
            parent = summaries.get((epic.rollup_mode, epic.rollup_key.upper()))
            self.verify_outline_parent(task, parent, context)
            self.verify_managed_resource_assignment(task, epic.resource_group, resources)
            if epic.drives_schedule:
                predecessors = [tasks.get(key.upper()) for key in epic.predecessors]
                if any(item is None for item in predecessors):
                    raise ProjectAutomationError(f"Cannot verify predecessor tasks for {epic.key}.")
                error = verify_project_predecessors(
                    task, [str(item.ID) for item in predecessors], predecessors
                )
                if error:
                    raise ProjectAutomationError(f"Dependency verification failed for {epic.key}: {error}")
        total_summaries = len(plan.summaries)
        for index, summary in enumerate(plan.summaries.values(), start=1):
            if index == 1 or index % 50 == 0 or index == total_summaries:
                project_progress(f"Summary verification progress: {index}/{total_summaries} row(s)")
            task = summaries.get((summary.rollup_mode, summary.key.upper()))
            if task is None:
                raise ProjectAutomationError(f"Saved sandbox is missing rollup {summary.key}.")
            for field, value in summary_assignments(summary, config):
                if field == "PercentComplete":
                    continue  # Native summary completion is recalculated by Project from child durations.
                verify_project_value(task, field, value, f"rollup={summary.key}")
                count += 1
        project_progress(f"Project data verification complete: {total_epics} epic(s), {total_summaries} summary row(s)")
        return count

    def configure_custom_fields(self, config: Dict[str, Any]) -> None:
        self.assert_project_identity()
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
                require_project_command(self.app.CustomFieldRename(field_id, friendly_name), "rename custom field")
            except Exception as exc:
                raise ProjectAutomationError(
                    f"Could not configure Project custom field {project_field} as {friendly_name}: {exc}"
                ) from exc
        if configured:
            project_progress("Custom Project field configuration complete")

    def snapshot_tasks(self, config: Dict[str, Any], progress_label: Optional[str] = None) -> Dict[str, ProjectTaskSnapshot]:
        snapshots: Dict[str, ProjectTaskSnapshot] = {}
        fields = config.get("project_fields", {})
        task_list = self.iter_tasks(progress_label=f"{progress_label} task scan" if progress_label else None)
        self.index_tasks_by_key(config, task_list)
        tasks_by_id = {str(safe_get(task, "ID")): task for task in task_list}
        total = len(task_list)
        for index, task in enumerate(task_list, start=1):
            if progress_label and (index == 1 or index % 50 == 0 or index == total):
                project_progress(f"{progress_label} progress: {index}/{total} row(s)")
            jira_key = safe_get(task, fields.get("jira_key", "Text1"))
            j2p_key = safe_get(task, fields.get("j2p_key", "Text10"))
            rollup_key = safe_get(task, fields.get("rollup_key", "Text5"))
            snapshot_key = j2p_key or jira_key or rollup_key
            if not snapshot_key:
                continue
            if str(snapshot_key).upper() in snapshots:
                raise ProjectAutomationError(f"Duplicate Project snapshot key {snapshot_key}: {project_task_context(task)}")
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
                predecessors=snapshot_relationship_keys(task, "Predecessors", tasks_by_id, config),
                successors=snapshot_relationship_keys(task, "Successors", tasks_by_id, config),
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
        self.assert_project_identity()
        self.index_tasks_by_key(config)  # Reject ambiguous rows before any task mutation.
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
            self.assert_project_identity()
            if index == 1 or index % 50 == 0 or index == total_epics:
                project_progress(f"Epic row write progress: {index}/{total_epics} row(s)")
            parent_summary_id = summary_id(epic.rollup_mode, epic.rollup_key)
            task = task_by_key.get(epic.key)
            try:
                if task is None:
                    task = self.add_epic_under_summary(epic, summary_tasks[parent_summary_id])
                else:
                    task = self.ensure_epic_under_summary(task, epic, summary_tasks[parent_summary_id], config, plan)
            except Exception:
                raise ProjectAutomationError(
                    f"Microsoft Project rejected epic row creation/placement: {epic_context(epic)}. "
                    "Check the sandbox outline and task restrictions."
                ) from None
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
            write_required_project_value(task, "Manual", False, project_task_context(task))

    def iter_tasks(self, progress_label: Optional[str] = None) -> List[Any]:
        tasks = []
        if self.project is None:
            return tasks
        try:
            total = int(self.project.Tasks.Count)
        except Exception as exc:
            raise ProjectAutomationError(f"Could not read Project task collection count: {exc}") from exc
        for index in range(1, total + 1):
            if progress_label and (index == 1 or index % 100 == 0 or index == total):
                project_progress(f"{progress_label}: {index}/{total} row(s)")
            try:
                task = self.project.Tasks(index)
            except Exception as exc:
                raise ProjectAutomationError(
                    f"Could not read Project task row {index} of {total}"
                    f" during {progress_label or 'task scan'}: {exc}"
                ) from exc
            if task is not None:
                tasks.append(task)
        return tasks

    def index_tasks_by_key(self, config: Dict[str, Any], task_list: Optional[List[Any]] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        key_field = config.get("project_fields", {}).get("jira_key", "Text1")
        j2p_key_field = config.get("project_fields", {}).get("j2p_key", "Text10")
        for task in self.iter_tasks() if task_list is None else task_list:
            key = safe_get(task, j2p_key_field) or safe_get(task, key_field)
            if key:
                normalized = str(key).strip().upper()
                if normalized in result:
                    raise ProjectAutomationError(
                        f"Duplicate Project key {normalized}: {project_task_context(result[normalized])}; "
                        f"{project_task_context(task)}. Resolve duplicate matching keys in the source schedule."
                    )
                result[normalized] = task
        return result

    def index_rollup_summaries(self, config: Dict[str, Any], task_list: List[Any]) -> Dict[Tuple[str, str], Any]:
        """Cache summary lookup for one readback pass; discard before reopening COM."""
        fields = config.get("project_fields", {})
        result: Dict[Tuple[str, str], Any] = {}
        for task in task_list:
            issue_type = str(safe_get(task, fields.get("jira_issue_type", "Text3")))
            if issue_type not in {"Initiative", "FixVersion"} and not safe_bool(safe_get(task, "Summary")):
                continue
            mode = str(safe_get(task, fields.get("rollup_mode", "Text4")))
            key = str(safe_get(task, fields.get("rollup_key", "Text5"))).upper()
            if not mode or not key:
                continue
            identity = (mode, key)
            if identity in result:
                raise ProjectAutomationError(
                    f"Duplicate Project rollup {mode}:{key}: "
                    f"{project_task_context(result[identity])}; {project_task_context(task)}. "
                    "Resolve duplicate rollup rows in the source schedule."
                )
            result[identity] = task
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
                try:
                    task = self.project.Tasks.Add(summary.name)
                except Exception:
                    raise ProjectAutomationError(
                        f"Microsoft Project rejected summary creation: rollup={summary.key}, "
                        f"field=Name, {value_metadata(summary.name)}."
                    ) from None
            context = f"rollup={summary.key}"
            write_required_project_value(task, "Manual", False, context)
            for field, value in summary_assignments(summary, config):
                write_required_project_value(task, field, value, context)
            summary_tasks[summary.summary_id] = task
        return summary_tasks

    def find_rollup_summary(self, rollup_mode: str, rollup_key: str, config: Dict[str, Any]) -> Optional[Any]:
        mode_field = config.get("project_fields", {}).get("rollup_mode", "Text4")
        rollup_field = config.get("project_fields", {}).get("rollup_key", "Text5")
        for task in self.iter_tasks():
            same_mode = str(safe_get(task, mode_field)) == rollup_mode
            same_key = str(safe_get(task, rollup_field)).upper() == rollup_key.upper()
            issue_type = str(safe_get(task, config.get("project_fields", {}).get("jira_issue_type", "Text3")))
            if same_mode and same_key and (safe_bool(safe_get(task, "Summary")) or issue_type in {"Initiative", "FixVersion"}):
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
            except Exception as exc:
                raise ProjectAutomationError(f"Could not indent {epic_context(epic)} under its rollup.") from exc
        self.verify_outline_parent(task, summary_task, epic_context(epic))
        return task

    def verify_outline_parent(self, task: Any, summary_task: Any, context: str) -> None:
        actual = safe_get(task, "OutlineParent")
        if summary_task is None or not actual or project_task_identity(actual) != project_task_identity(summary_task):
            raise ProjectAutomationError(f"Microsoft Project did not retain the required outline parent: {context}.")

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
            self.verify_outline_parent(task, summary_task, epic_context(epic))
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
            self.verify_outline_parent(moved_task, summary_task, epic_context(epic))
            return moved_task
        except Exception as exc:
            raise ProjectAutomationError(
                f"Could not move {epic_context(epic)} under rollup {epic.rollup_key}: {exc}"
            ) from exc

    def update_epic_task(self, task: Any, epic: PlanEpic, config: Dict[str, Any], plan: RunPlan) -> None:
        fields = config.get("project_fields", {})
        write_required_project_value(task, "Manual", False, epic_context(epic))
        for field, value in epic_assignments(epic, config):
            write_required_project_value(task, field, value, epic_context(epic))
        try:
            self.set_native_resource_group(task, epic.resource_group)
        except Exception as exc:
            raise ProjectAutomationError(
                f"Microsoft Project rejected epic resource assignment: {epic_context(epic)}, "
                f"field=ResourceGroup, {value_metadata(epic.resource_group)}. {exc}"
            ) from exc
        for warning in getattr(self, "resource_assignment_warnings", []):
            plan.audit_items.append(
                AuditItem("Warning", "ProjectUnmanagedResourcePreserved", jira_key=epic.jira_key or epic.key,
                          schedule_key=epic.key, issue_type="Epic", summary=epic.summary,
                          field="Resource Group", color="review_needed", message=warning,
                          reviewer_action="Review legacy resource assignments manually; j2p only replaces resources bearing its ownership marker.")
            )
        self.write_project_date(task, epic, plan, fields.get("jira_target_start", "Date1"), "Jira Target Start", "Start")
        self.write_project_date(task, epic, plan, fields.get("jira_target_end", "Date2"), "Jira Target End", "Finish")
        write_required_project_value(task, "Active", not epic.completed and epic.drives_schedule, epic_context(epic))
        try:
            task.HideBar = bool(epic.completed)
        except Exception:
            pass  # Cosmetic only; Active and Manual are required above.

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
        self.resource_assignment_warnings = []
        desired = self.ensure_group_resource(resource_group) if resource_group else None
        assignments = self.resource_assignments(task)
        if desired is not None and not self.task_has_resource(task, desired):
            if not self.assign_resource_to_task(task, desired):
                raise ProjectAutomationError(f"Could not assign j2p resource group {resource_group}.")
        # Delete only assignments whose resource carries j2p's persisted ownership marker.
        for assignment, resource in assignments:
            if is_managed_group_resource(resource):
                if desired is None or int(resource.ID) != int(desired.ID):
                    try:
                        require_project_command(assignment.Delete(), "remove previous j2p resource assignment")
                    except Exception as exc:
                        raise ProjectAutomationError("Could not remove the previous managed resource assignment.") from exc
            elif safe_get(resource, "Group"):
                self.resource_assignment_warnings.append(
                    f"Preserved unmanaged resource '{safe_get(resource, 'Name')}' with group "
                    f"'{safe_get(resource, 'Group')}'. Native Resource Group may contain multiple groups."
                )
        self.verify_managed_resource_assignment(task, resource_group)

    def resource_assignments(self, task: Any, resources_by_id: Optional[Dict[int, Any]] = None) -> List[Tuple[Any, Any]]:
        result = []
        try:
            resources = ({int(resource.ID): resource for resource in self.iter_resources()}
                         if resources_by_id is None else resources_by_id)
            for index in range(1, int(task.Assignments.Count) + 1):
                assignment = task.Assignments(index)
                resource = resources.get(int(assignment.ResourceID))
                if resource is None:
                    raise ProjectAutomationError("An assigned Project resource could not be resolved.")
                result.append((assignment, resource))
        except Exception as exc:
            raise ProjectAutomationError("Could not read Project resource assignments safely.") from exc
        return result

    def iter_resources(self) -> List[Any]:
        try:
            return [resource for index in range(1, int(self.project.Resources.Count) + 1)
                    for resource in [self.project.Resources(index)] if resource is not None]
        except Exception as exc:
            raise ProjectAutomationError("Could not read the Project resource collection.") from exc

    def verify_managed_resource_assignment(self, task: Any, resource_group: str,
                                         resources_by_id: Optional[Dict[int, Any]] = None) -> None:
        owned = [resource for _assignment, resource in self.resource_assignments(task, resources_by_id)
                 if is_managed_group_resource(resource)]
        expected = 1 if resource_group else 0
        if len(owned) != expected or (owned and (
            str(safe_get(owned[0], "Group")) != resource_group
            or resource_ownership_notes(owned[0]) != managed_resource_marker(resource_group)
        )):
            raise ProjectAutomationError(
                f"Project did not retain exactly {expected} managed resource assignment(s) for group '{resource_group}'."
            )

    def ensure_group_resource(self, resource_group: str) -> Any:
        marker = managed_resource_marker(resource_group)
        matches = [resource for resource in self.iter_resources() if resource_ownership_notes(resource) == marker]
        if len(matches) > 1:
            raise ProjectAutomationError(f"Duplicate managed Project resources for group '{resource_group}'.")
        if matches:
            resource = matches[0]
        else:
            name = resource_group
            if self.find_resource(name) is not None:
                digest = hashlib.sha256(resource_group.encode("utf-8")).hexdigest()[:12]
                name = f"[j2p:{digest}] {resource_group}"[:255]
                if self.find_resource(name) is not None:
                    raise ProjectAutomationError(f"Reserved j2p resource name already belongs to an unmanaged resource: {name}")
            try:
                resource = self.project.Resources.Add(name)
            except Exception as exc:
                raise ProjectAutomationError(f"Could not create j2p Project resource '{name}'.") from exc
            write_required_project_value(resource, "Notes", marker, f"resource_group={resource_group}")
        write_required_project_value(resource, "Group", resource_group, f"resource_group={resource_group}")
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
            self.assert_project_identity()
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
        if not verify_project_predecessors(task, expected_ids, predecessor_tasks):
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
            return verify_project_predecessors(task, expected_ids, predecessor_tasks)

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
            readback_error = verify_project_predecessors(task, expected_ids, predecessor_tasks)
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
            readback_error = verify_project_predecessors(task, expected_ids, predecessor_tasks)
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
            return verify_project_predecessors(task, expected_ids, predecessor_tasks)

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
            readback_error = verify_project_predecessors(task, expected_ids, predecessor_tasks)
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
                dependencies.Add(predecessor_task, 1, 0)
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
        self.assert_project_identity()
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
        ("completion_percent", fields.get("completion_percent", "Number7")),
        ("completion_total_story_points", fields.get("completion_total_story_points", "Number5")),
        ("completion_completed_story_points", fields.get("completion_completed_story_points", "Number6")),
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
    return result is False or (isinstance(result, (int, float)) and result == 0)


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
    return [task_id for task_id, _kind, _lag in parse_project_dependency_text(value)]


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


def verify_project_predecessors(
    task: Any, expected_ids: List[str], predecessor_tasks: Optional[List[Any]] = None
) -> str:
    current_value = str(safe_get(task, "Predecessors"))
    try:
        links = project_predecessor_links(task)
    except ProjectAutomationError as exc:
        return str(exc)
    current_ids = [link[0] for link in links]
    missing_ids = [key for key in expected_ids if key not in current_ids]
    unexpected_ids = [key for key in current_ids if key not in expected_ids]
    if missing_ids:
        return (f"Microsoft Project did not retain predecessor ID(s) {', '.join(missing_ids)}. "
                f"Current Project value: '{current_value}'.")
    if unexpected_ids:
        return (f"Microsoft Project retained unexpected predecessor ID(s) {', '.join(unexpected_ids)}. "
                f"Current Project value: '{current_value}'.")
    if len(current_ids) != len(set(current_ids)):
        return "Microsoft Project retained duplicate predecessor links."
    expected_identity = {str(item.ID): project_task_identity(item) for item in predecessor_tasks or []}
    for task_id, identity, link_type, lag in links:
        if link_type != 1 or not project_lag_is_zero(lag):
            return f"Predecessor ID {task_id} must be Finish-to-Start with zero lag; type={link_type}, lag={lag}."
        if identity and task_id in expected_identity and identity != expected_identity[task_id]:
            return f"Predecessor ID {task_id} resolves to a different Project task identity."
    return ""


def project_predecessor_links(task: Any) -> List[Tuple[str, str, int, Any]]:
    """Read stable object identities and semantics; text parsing is a checked fallback."""
    collection = safe_get(task, "TaskDependencies")
    try:
        count = int(collection.Count)
    except (AttributeError, TypeError, ValueError):
        count = None
    except Exception as exc:
        raise ProjectAutomationError("Could not read Project dependency collection.") from exc
    if count is not None:
        links = []
        for index in range(1, count + 1):
            try:
                dependency = collection(index)
                source = getattr(dependency, "From")
                target = dependency.To
                if project_task_identity(target) != project_task_identity(task):
                    continue
                links.append((str(source.ID), project_task_identity(source), int(dependency.Type), dependency.Lag))
            except Exception as exc:
                raise ProjectAutomationError("Could not read complete Project dependency identity/type/lag.") from exc
        return links
    try:
        predecessor_text = str(task.Predecessors)
    except Exception as exc:
        raise ProjectAutomationError("Could not read Project predecessor fields or object-model links.") from exc
    return [(task_id, "", link_type, lag) for task_id, link_type, lag in parse_project_dependency_text(predecessor_text)]


def parse_project_dependency_text(value: str) -> List[Tuple[str, int, str]]:
    if not value.strip():
        return []
    result = []
    types = {"FF": 0, "FS": 1, "SF": 2, "SS": 3}
    for token in re.split(r"[,;]", value):
        match = re.fullmatch(r"\s*(\d+)\s*(FS|SS|FF|SF)?\s*([+-]\s*\d+(?:\.\d+)?(?:[a-zA-Z%]+)?)?\s*", token, re.I)
        if not match:
            raise ProjectAutomationError(
                f"Cannot verify Project dependency text {token!r}; its object-model relationship data is unavailable."
            )
        result.append((match[1], types[(match[2] or "FS").upper()], (match[3] or "0").replace(" ", "")))
    return result


def project_lag_is_zero(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value == 0
    return re.fullmatch(r"[+-]?0+(?:\.0+)?[a-zA-Z%]*", str(value).strip()) is not None


def project_task_identity(task: Any) -> str:
    unique_id = safe_int(safe_get(task, "UniqueID"))
    if unique_id > 0:
        external = "external:" if safe_bool(safe_get(task, "ExternalTask")) else ""
        return f"{external}uid:{unique_id}"
    task_id = safe_int(safe_get(task, "ID"))
    if task_id > 0:
        return f"id:{task_id}"
    raise ProjectAutomationError("Project task has no readable positive ID or UniqueID.")


def project_task_context(task: Any) -> str:
    return f"Project row={safe_get(task, 'ID')}, UniqueID={safe_get(task, 'UniqueID')}"


def snapshot_relationship_keys(
    task: Any, direction: str, tasks_by_id: Dict[str, Any], config: Dict[str, Any]
) -> List[str]:
    field = "PredecessorTasks" if direction == "Predecessors" else "SuccessorTasks"
    related = safe_get(task, field)
    try:
        count = int(related.Count)
    except (AttributeError, TypeError, ValueError):
        count = None
    except Exception as exc:
        raise ProjectAutomationError(f"Could not read {field} for {project_task_context(task)}.") from exc
    if count is not None:
        try:
            targets = [related(index) for index in range(1, count + 1)]
        except Exception as exc:
            raise ProjectAutomationError(f"Could not enumerate {field} for {project_task_context(task)}.") from exc
    else:
        text = str(safe_get(task, direction))
        # Legacy/custom views may expose Jira keys directly.
        explicit_keys = parse_project_key_list(text)
        if explicit_keys and not re.match(r"^\s*\d", text):
            return explicit_keys
        targets = []
        for task_id, _kind, _lag in parse_project_dependency_text(text):
            target = tasks_by_id.get(task_id)
            if target is None:
                raise ProjectAutomationError(f"Cannot resolve {direction} task ID {task_id} in the Project snapshot.")
            targets.append(target)
    fields = config.get("project_fields", {})
    keys = []
    for target in targets:
        key = safe_get(target, fields.get("j2p_key", "Text10")) or safe_get(target, fields.get("jira_key", "Text1"))
        keys.append(str(key).upper() if key else "PROJECT:" + project_task_identity(target))
    return sorted(set(keys))


def require_project_command(result: Any, operation: str) -> None:
    if project_call_failed(result):
        raise ProjectAutomationError(f"Microsoft Project returned False while attempting to {operation}.")


def verify_project_value(task: Any, field: str, expected: Any, context: str) -> None:
    try:
        actual = getattr(task, field)
    except Exception:
        raise ProjectAutomationError(f"Microsoft Project field readback failed: {context}, field={field}.") from None
    if isinstance(expected, bool):
        if isinstance(actual, str):
            normalized = actual.strip().lower()
            parsed = True if normalized in {"true", "yes", "1", "-1"} else False if normalized in {"false", "no", "0"} else None
        elif isinstance(actual, (bool, int, float)) and actual in (0, 1, -1):
            parsed = bool(actual)
        else:
            parsed = None
        matches = parsed is expected
    elif isinstance(expected, (int, float)):
        try:
            matches = math.isclose(float(actual), float(expected), rel_tol=1e-8, abs_tol=1e-6)
        except (TypeError, ValueError):
            matches = False
    else:
        matches = str(actual) == str(expected)
    if not matches:
        raise ProjectAutomationError(
            f"Microsoft Project did not retain the requested value: {context}, field={field}, {value_metadata(expected)}."
        )


def write_required_project_value(task: Any, field: str, value: Any, context: str) -> None:
    try:
        setattr(task, field, value)
    except Exception:
        raise ProjectAutomationError(
            f"Microsoft Project rejected write: {context}, field={field}, {value_metadata(value)}. "
            "Check field mapping, formula/lookup restrictions, and Project edition capabilities."
        ) from None
    verify_project_value(task, field, value, context)


def summary_assignments(summary: Any, config: Dict[str, Any]) -> List[Tuple[str, Any]]:
    fields = config.get("project_fields", {})
    values = [("Name", summary.name)]
    if summary.rollup_mode == "initiative":
        values.append((fields.get("jira_key", "Text1"), summary.key))
    values.extend([
        (fields.get("jira_issue_type", "Text3"), "Initiative" if summary.rollup_mode == "initiative" else "FixVersion"),
        (fields.get("rollup_mode", "Text4"), summary.rollup_mode),
        (fields.get("rollup_key", "Text5"), summary.key),
        (fields.get("total_story_points", "Number1"), summary.total_story_points),
        (fields.get("completed_story_points", "Number2"), summary.completed_story_points),
        (fields.get("logged_hours", "Number3"), summary.logged_hours),
        (story_point_ratio_project_field(config), summary.story_point_ratio),
        (fields.get("completion_total_story_points", "Number5"), summary.completion_total_story_points),
        (fields.get("completion_completed_story_points", "Number6"), summary.completion_completed_story_points),
        (fields.get("completion_percent", "Number7"), summary.percent_complete),
        ("PercentComplete", summary.percent_complete),
    ])
    return values


def managed_resource_marker(resource_group: str) -> str:
    return "j2p-managed-resource-v1:" + hashlib.sha256(resource_group.encode("utf-8")).hexdigest()


def resource_ownership_notes(resource: Any) -> str:
    try:
        return str(resource.Notes)
    except Exception:
        raise ProjectAutomationError(
            f"Could not read resource ownership marker: resource={safe_get(resource, 'Name')}."
        ) from None


def is_managed_group_resource(resource: Any) -> bool:
    return re.fullmatch(r"j2p-managed-resource-v1:[0-9a-f]{64}", resource_ownership_notes(resource)) is not None


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
