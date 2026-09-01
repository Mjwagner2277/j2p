from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from j2p.cli import build_parser, main
from j2p.config import ConfigError, load_config
from j2p.core import (
    J2PError,
    PlanEpic,
    RunPlan,
    ProjectTaskSnapshot,
    build_run_plan,
    run_plan_to_state,
    snapshots_from_state,
    write_json,
)
from j2p.project import (
    MicrosoftProjectSession,
    ProjectAutomationError,
    append_resource_name,
    project_column_for_audit_field,
    project_date_for_com,
)
from j2p.reports import write_reports


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class J2PPlanningTests(unittest.TestCase):
    def test_initiative_mode_rollups_dependencies_and_exclusions(self) -> None:
        config = load_config(EXAMPLES / "config.example.yaml")
        baseline = {
            "TEAM-101": ProjectTaskSnapshot(
                key="TEAM-101",
                name="Identity and access foundation",
                issue_type="Epic",
                rollup_key="PROD-100",
                resource_group="Product Delivery",
                total_story_points=13,
                completed_story_points=5,
                percent_complete=38,
                status="In Progress",
                target_end="2026-09-30",
            ),
            "TEAM-103": ProjectTaskSnapshot(
                key="TEAM-103",
                name="Billing integration",
                issue_type="Epic",
                rollup_key="PROD-100",
                resource_group="Product Delivery",
                total_story_points=13,
                completed_story_points=0,
                percent_complete=0,
                status="To Do",
            ),
            "TEAM-999": ProjectTaskSnapshot(
                key="TEAM-999",
                name="Legacy schedule item",
                issue_type="Epic",
                rollup_key="PROD-100",
            ),
        }
        plan = build_run_plan(EXAMPLES / "project-wide-jira-update.csv", config, baseline)

        self.assertEqual(plan.rollup_mode, "mixed")
        self.assertIn("TEAM-101", plan.epics)
        self.assertEqual(plan.epics["TEAM-101"].percent_complete, 100)
        self.assertEqual(plan.epics["TEAM-102"].percent_complete, 38)
        self.assertEqual(plan.epics["TEAM-103"].rollup_key, "PLAT-100")
        self.assertEqual(plan.epics["PLAT-201"].rollup_mode, "fixVersion")
        self.assertEqual(plan.epics["PLAT-201"].rollup_key, "Portal 2026")
        self.assertEqual(plan.epics["TEAM-102"].predecessors, ["TEAM-101"])
        self.assertIn("TEAM-103", plan.epics["TEAM-102"].successors)
        self.assertNotIn("TEAM-105", plan.epics)
        self.assertNotIn("UNK-106", plan.epics)

        categories = {item.category for item in plan.audit_items}
        self.assertIn("ChangedName", categories)
        self.assertIn("RollupMove", categories)
        self.assertIn("CompletedSinceLastUpdate", categories)
        self.assertIn("UnmatchedProjectTask", categories)
        self.assertIn("ExcludedMissingRollup", categories)
        self.assertIn("ExcludedUnknownPrefix", categories)
        self.assertIn("MissingDependencyTarget", categories)

    def test_fixversion_mode_defaults_multi_fixversion_epics_to_reference_rows(self) -> None:
        config = load_config(EXAMPLES / "config.fixversion.example.yaml")
        plan = build_run_plan(EXAMPLES / "project-wide-jira-fixversion.csv", config)

        self.assertEqual(plan.rollup_mode, "fixVersion")
        self.assertEqual(plan.epics["TEAM-501"].rollup_key, "Portal 2026")
        self.assertIn("fixVersion:Portal 2026", plan.summaries)
        self.assertIn("TEAM-503", plan.epics)
        reference_key = "TEAM-503::FV::PORTAL-2027::BF5F3312"
        self.assertIn(reference_key, plan.epics)
        self.assertEqual(plan.epics["TEAM-503"].jira_key, "TEAM-503")
        self.assertEqual(plan.epics["TEAM-503"].row_role, "Primary")
        self.assertTrue(plan.epics["TEAM-503"].drives_schedule)
        self.assertEqual(plan.epics[reference_key].jira_key, "TEAM-503")
        self.assertEqual(plan.epics[reference_key].row_role, "Reference")
        self.assertFalse(plan.epics[reference_key].drives_schedule)
        categories = {item.category for item in plan.audit_items}
        self.assertIn("MultiFixVersionReference", categories)
        self.assertNotIn("ExcludedMissingRollup", categories)

    def test_fixversion_policy_can_split_multi_fixversion_epics(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "DATA-1,1,Epic,Qualification and shop deliverable,,,\"Qual Event 1;Shop Drop A\",,In Progress,,2026-01-01,2026-01-15,,",
                "DATA-11,2,Story,Qual prep,DATA-1,,Qual Event 1,3,Done,Done,,,,",
                "DATA-12,3,Story,Shop prep,DATA-1,,Shop Drop A,5,To Do,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "split.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(EXAMPLES / "config.fixversion.example.yaml")
            plan = build_run_plan(csv_path, config)

        split_keys = [epic.key for epic in plan.epics.values()]
        self.assertIn("DATA-1", split_keys)
        self.assertIn("DATA-1::FV::SHOP-DROP-A::72104C44", split_keys)
        self.assertTrue(all(epic.row_role == "Split" for epic in plan.epics.values()))
        self.assertTrue(all(epic.drives_schedule for epic in plan.epics.values()))
        self.assertIn("MultiFixVersionSplit", {item.category for item in plan.audit_items})

    def test_reference_rollup_shows_progress_without_counting_story_points_twice(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "PLAT-1,1,Epic,Qualification and shop visibility,,,\"Qual Event 1;Shop Drop A\",,In Progress,,2026-01-01,2026-01-15,,",
                "PLAT-11,2,Story,Qual prep,PLAT-1,,Qual Event 1,3,Done,Done,,,,",
                "PLAT-12,3,Story,Shop prep,PLAT-1,,Shop Drop A,5,To Do,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "reference.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(EXAMPLES / "config.example.yaml")
            plan = build_run_plan(csv_path, config)

        reference_summary = plan.summaries["fixVersion:Shop Drop A"]
        self.assertEqual(reference_summary.total_story_points, 0)
        self.assertEqual(reference_summary.completed_story_points, 0)
        self.assertEqual(reference_summary.reference_epic_count, 1)
        self.assertEqual(reference_summary.percent_complete, 38)

    def test_old_multiple_fixversion_behavior_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "multiple_fix_versions is no longer supported"):
            load_config(None, {"behavior": {"multiple_fix_versions": "exclude"}})

    def test_multi_fixversion_policy_normalizes_case_and_whitespace(self) -> None:
        config = load_config(None, {"multi_fixversion_policy": {"default": " Reference ", "ops": "SPLIT"}})

        self.assertEqual(config["multi_fixversion_policy"]["default"], "reference")
        self.assertEqual(config["multi_fixversion_policy"]["OPS"], "split")

    def test_validate_cli_writes_manager_and_audit_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            buffer = StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "validate",
                        "--jira-csv",
                        str(EXAMPLES / "project-wide-jira-update.csv"),
                        "--config",
                        str(EXAMPLES / "config.example.yaml"),
                        "--output-dir",
                        temp,
                        "--run-id",
                        "unit",
                    ]
                )
            self.assertEqual(exit_code, 0)
            output = buffer.getvalue()
            self.assertIn("[j2p]", output)
            self.assertIn("Reading Jira CSV and building review plan", output)
            self.assertIn("Writing manager report and audit CSV files", output)
            run_dir = Path(temp) / "j2p-run-unit"
            self.assertTrue((run_dir / "Manager-Review-Report.html").exists())
            self.assertTrue((run_dir / "audit-detail.csv").exists())
            self.assertTrue((run_dir / "planned-epics.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "TEAM" / "audit-detail.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "TEAM" / "planned-epics.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "PLAT" / "summary-rollups.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "UNK" / "audit-detail.csv").exists())
            report = (run_dir / "Manager-Review-Report.html").read_text(encoding="utf-8")
            self.assertIn("Reviewer Action Needed", report)
            self.assertIn("Rollup Status", report)
            self.assertIn("Review Type Summary", report)
            self.assertIn("Color Key", report)
            self.assertIn("Color Case Examples", report)
            self.assertIn("Project Key Rollup Mapping", report)
            self.assertIn("<details class=\"detail-block\">", report)
            self.assertIn("Full Planned Epic Rows", report)
            self.assertLess(report.index("Rollup Status"), report.index("Reviewer Action Needed"))
            self.assertLess(report.index("Reviewer Action Needed"), report.index("Full Planned Epic Rows"))
            self.assertIn("Unknown team epic", report)
            audit_header = (run_dir / "audit-detail.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("project_key", audit_header)
            self.assertIn("schedule_key", audit_header)

    def test_debug_visible_replaces_visible_in_help(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertNotIn("--visible", help_text)
        self.assertNotIn("--debug-visible", help_text)

        subparsers_action = next(action for action in parser._actions if getattr(action, "choices", None))
        update_help = subparsers_action.choices["update"].format_help()
        self.assertIn("--debug-visible", update_help)
        self.assertNotIn("--visible ", update_help)

    def test_circular_dependencies_are_skipped_and_reported(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "PROD-1,1,Initiative,Program Alpha,,,,,In Progress,,,,,",
                "TEAM-1,2,Epic,First epic,,PROD-1,,0,In Progress,,2026-01-01,2026-01-15,TEAM-2,",
                "TEAM-2,3,Epic,Second epic,,PROD-1,,0,In Progress,,2026-01-16,2026-01-31,TEAM-1,",
                "TEAM-11,4,Story,First story,TEAM-1,,,3,Done,,,,,",
                "TEAM-21,5,Story,Second story,TEAM-2,,,5,Done,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "cycle.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(EXAMPLES / "config.example.yaml")
            plan = build_run_plan(csv_path, config)
            self.assertIn("CircularDependencySkipped", {item.category for item in plan.audit_items})
            edge_count = len(plan.epics["TEAM-1"].successors) + len(plan.epics["TEAM-2"].successors)
            self.assertEqual(edge_count, 1)

    def test_missing_required_columns_fail_clearly(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue Type,Summary,Status",
                "TEAM-1,Epic,Missing parent and points,To Do",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "missing-columns.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(EXAMPLES / "config.example.yaml")
            with self.assertRaisesRegex(J2PError, "missing required mapped columns"):
                build_run_plan(csv_path, config)

    def test_story_without_epic_link_is_reported_and_not_counted(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "PROD-1,1,Initiative,Program Alpha,,,,,In Progress,,,,,",
                "TEAM-1,2,Epic,First epic,,PROD-1,,0,In Progress,,2026-01-01,2026-01-15,,",
                "TEAM-11,4,Story,Orphan story,,,,3,Done,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "orphan.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(EXAMPLES / "config.example.yaml")
            plan = build_run_plan(csv_path, config)
            self.assertEqual(plan.epics["TEAM-1"].total_story_points, 0)
            categories = {item.category for item in plan.audit_items}
            self.assertIn("StoryMissingEpicLink", categories)
            self.assertIn("InPlanning", categories)

    def test_manager_report_is_self_contained_and_field_mapping_includes_status(self) -> None:
        config = load_config(EXAMPLES / "config.example.yaml")
        plan = build_run_plan(EXAMPLES / "project-wide-jira-update.csv", config)
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            paths = write_reports(plan, run_dir, config)
            report = paths["manager_report"].read_text(encoding="utf-8")
            self.assertNotIn("<script src=", report)
            self.assertNotIn("<link rel=", report)
            self.assertNotIn("https://", report)
            field_mapping = paths["field_mapping"].read_text(encoding="utf-8")
            self.assertIn("Native Project fields used by j2p", field_mapping)
            self.assertIn("| Resource Group | Resource Group |", field_mapping)
            self.assertIn("Jira Status", field_mapping)
            self.assertNotIn("| `resource_group` | `Text6` |", field_mapping)

    def test_state_round_trip_can_be_used_as_baseline(self) -> None:
        config = load_config(EXAMPLES / "config.example.yaml")
        initial = build_run_plan(EXAMPLES / "project-wide-jira-initial.csv", config)
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "j2p-state.json"
            write_json(state_path, run_plan_to_state(initial))
            baseline = snapshots_from_state(state_path)
            follow_on = build_run_plan(EXAMPLES / "project-wide-jira-update.csv", config, baseline)
            categories = {item.category for item in follow_on.audit_items}
            self.assertIn("ChangedName", categories)
            self.assertIn("CompletedSinceLastUpdate", categories)

    def test_large_1200_line_scenario_covers_manager_review_cases(self) -> None:
        baseline_csv = EXAMPLES / "large-scenario" / "project-wide-jira-baseline-1200.csv"
        updated_csv = EXAMPLES / "large-scenario" / "project-wide-jira-updated-1200.csv"
        with baseline_csv.open("r", encoding="utf-8") as handle:
            self.assertEqual(sum(1 for _line in handle), 1200)
        with updated_csv.open("r", encoding="utf-8") as handle:
            self.assertEqual(sum(1 for _line in handle), 1200)

        config = load_config(EXAMPLES / "large-scenario" / "config.large-example.yaml")
        baseline = build_run_plan(baseline_csv, config)
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "j2p-state.json"
            write_json(state_path, run_plan_to_state(baseline))
            follow_on = build_run_plan(updated_csv, config, snapshots_from_state(state_path))

        self.assertEqual(follow_on.rollup_mode, "mixed")
        self.assertIn("CORE-1980", follow_on.epics)
        self.assertIn("WEB-2010", follow_on.epics)
        self.assertNotIn("CORE-1049", follow_on.epics)
        self.assertEqual(follow_on.epics["WEB-2010"].in_planning, True)
        self.assertEqual(follow_on.epics["PLAT-4000"].rollup_mode, "fixVersion")

        categories = {item.category for item in follow_on.audit_items}
        self.assertTrue(
            {
                "AddedEpic",
                "ChangedField",
                "ChangedName",
                "CircularDependencySkipped",
                "CompletedSinceLastUpdate",
                "CsvRowMissingJiraKey",
                "DependencyChange",
                "ExcludedMissingRollup",
                "ExcludedUnknownPrefix",
                "InPlanning",
                "MissingDependencyTarget",
                "RollupMove",
                "SelfDependencySkipped",
                "StoryMissingEpicLink",
                "UnmatchedProjectTask",
                "UnparsedDate",
            }.issubset(categories)
        )
        colors = {item.color for item in follow_on.audit_items if item.color}
        self.assertTrue({"changed_cell", "review_needed", "dependency_review", "in_planning"}.issubset(colors))

    def test_schedule_review_marks_cascade_root_red(self) -> None:
        config = load_config(EXAMPLES / "config.example.yaml")
        plan = RunPlan(
            generated_at="2026-01-01T00:00:00",
            jira_csv="unit.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={
                "TEAM-RED": PlanEpic(
                    key="TEAM-RED",
                    issue_id="1",
                    summary="Critical path root",
                    status="In Progress",
                    rollup_mode="initiative",
                    rollup_key="PROD-100",
                    rollup_name="Program",
                    resource_group="Product Delivery",
                    key_prefix="TEAM",
                    total_story_points=8,
                    completed_story_points=0,
                    percent_complete=0,
                    in_planning=False,
                    completed=False,
                    target_start="2026-01-01",
                    target_end="2026-01-31",
                ),
                "TEAM-CASCADE": PlanEpic(
                    key="TEAM-CASCADE",
                    issue_id="2",
                    summary="Downstream item",
                    status="In Progress",
                    rollup_mode="initiative",
                    rollup_key="PROD-100",
                    rollup_name="Program",
                    resource_group="Product Delivery",
                    key_prefix="TEAM",
                    total_story_points=8,
                    completed_story_points=0,
                    percent_complete=0,
                    in_planning=False,
                    completed=False,
                    target_start="2026-02-01",
                    target_end="2026-02-28",
                ),
            },
            audit_items=[],
        )
        before = {
            "TEAM-RED": ProjectTaskSnapshot(key="TEAM-RED", finish="2026-01-31"),
            "TEAM-CASCADE": ProjectTaskSnapshot(key="TEAM-CASCADE", finish="2026-02-28"),
        }
        after = {
            "TEAM-RED": ProjectTaskSnapshot(key="TEAM-RED", finish="2026-02-07"),
            "TEAM-CASCADE": ProjectTaskSnapshot(key="TEAM-CASCADE", finish="2026-03-07"),
        }
        session = object.__new__(MicrosoftProjectSession)
        session.snapshot_tasks = lambda _config: after
        session.task_is_critical = lambda key, _config: key == "TEAM-RED"

        MicrosoftProjectSession.add_schedule_review_items(session, plan, before, config)

        root = next(item for item in plan.audit_items if item.category == "CriticalPathCascadeRoot")
        downstream = next(item for item in plan.audit_items if item.category == "CascadingDateChange")
        self.assertEqual(root.jira_key, "TEAM-RED")
        self.assertEqual(root.color, "cascade_root")
        self.assertEqual(downstream.color, "changed_cell")

    def test_resource_group_uses_project_resource_assignment(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.project = FakeProject()
        task = FakeTask()

        MicrosoftProjectSession.set_native_resource_group(session, task, "Product Delivery")

        self.assertEqual(session.project.Resources.items[0].Name, "Product Delivery")
        self.assertEqual(session.project.Resources.items[0].Group, "Product Delivery")
        self.assertEqual(task.Assignments.items[0].ResourceID, 1)

    def test_close_project_uses_explicit_save_option(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeProjectApp()

        MicrosoftProjectSession.close_project(session, save_changes=False)

        self.assertEqual(session.app.close_ex_calls, [(0, True, False)])

    def test_save_as_passes_absolute_path_to_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            session = object.__new__(MicrosoftProjectSession)
            session.app = FakeProjectApp()
            output_path = Path(temp) / "nested" / "demo.mpp"

            MicrosoftProjectSession.save_as(session, output_path)

            self.assertTrue(output_path.parent.exists())
            self.assertEqual(session.app.save_as_paths, [str(output_path.resolve())])

    def test_new_uses_projects_add_without_dialogs(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeNewProjectApp()

        MicrosoftProjectSession.new(session)

        self.assertIs(session.project, session.app.project)
        self.assertEqual(session.app.Projects.add_calls, [(False, "", False)])
        self.assertEqual(session.app.view_calls, ["&Gantt Chart"])

    def test_new_can_fallback_to_filenew(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeFileNewOnlyApp()

        MicrosoftProjectSession.new(session)

        self.assertIs(session.project, session.app.project)
        self.assertEqual(session.app.file_new_calls, [(False, "", False, False)])

    def test_create_application_prefers_isolated_project_instance(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.win32com = FakeWin32Com()
        session.owns_app = False

        app = MicrosoftProjectSession.create_application(session)

        self.assertIs(app, session.win32com.dispatch_ex_app)
        self.assertTrue(session.owns_app)
        self.assertEqual(session.win32com.calls, ["DispatchEx"])

    def test_create_application_error_points_to_task_manager(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.win32com = FakeWin32Com(dispatch_ex_error=RuntimeError("stale Project"), dispatch_error=RuntimeError("busy"))
        session.owns_app = False

        with self.assertRaisesRegex(ProjectAutomationError, "Task Manager.*WINPROJ.EXE"):
            MicrosoftProjectSession.create_application(session)

    def test_application_visibility_is_best_effort(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeVisibilityRejectingApp()
        session.visible = False

        MicrosoftProjectSession.configure_application_window(session)

        self.assertFalse(session.app.display_alerts)

    def test_append_resource_name_preserves_existing_names(self) -> None:
        self.assertEqual(
            append_resource_name("Jane Smith, Product Delivery", "Platform Engineering"),
            "Jane Smith, Product Delivery, Platform Engineering",
        )
        self.assertEqual(
            append_resource_name("Jane Smith, Product Delivery", "Product Delivery"),
            "Jane Smith, Product Delivery",
        )

    def test_project_date_for_com_converts_iso_dates(self) -> None:
        self.assertIn("2026", str(project_date_for_com("2026-09-01", "Start")))
        self.assertIn("2026", str(project_date_for_com("2026-09-01", "Finish")))

    def test_project_date_for_com_rejects_project_out_of_range_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the Microsoft Project supported range"):
            project_date_for_com("1800-01-01", "Start")

    def test_project_column_for_audit_field_uses_stable_custom_field_ids(self) -> None:
        config = load_config(EXAMPLES / "config.example.yaml")

        self.assertEqual(project_column_for_audit_field("Rollup Key", config), "Text5")
        self.assertEqual(project_column_for_audit_field("Jira Target End", config), "Date2")
        self.assertEqual(project_column_for_audit_field("Resource Group", config), "Resource Group")

    def test_color_project_cell_sets_active_cell_background(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeFormattingApp()
        task = FakeTask()
        task.ID = 7

        self.assertTrue(MicrosoftProjectSession.color_project_cell(session, task, "Text1", "#C6EFCE"))
        self.assertEqual(session.app.select_calls, [("cell", 7, "Text1", False)])
        self.assertNotEqual(session.app.ActiveCell.CellColorEx, 0)

    def test_color_project_cell_can_fall_back_to_font32ex(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeFontFallbackFormattingApp()
        task = FakeTask()
        task.ID = 8

        self.assertTrue(MicrosoftProjectSession.color_project_cell(session, task, "Text2", "#FFC7CE"))
        self.assertEqual(session.app.font32_colors, [13551615])


class FakeProjectApp:
    def __init__(self) -> None:
        self.close_ex_calls = []
        self.save_as_paths = []

    def FileCloseEx(self, Save: int, NoAuto: bool, CheckIn: bool) -> None:
        self.close_ex_calls.append((Save, NoAuto, CheckIn))

    def FileSaveAs(self, Name: str) -> None:
        self.save_as_paths.append(Name)


class FakeWin32Com:
    def __init__(self, dispatch_ex_error: Exception = None, dispatch_error: Exception = None) -> None:
        self.dispatch_ex_error = dispatch_ex_error
        self.dispatch_error = dispatch_error
        self.dispatch_ex_app = object()
        self.dispatch_app = object()
        self.calls = []

    def DispatchEx(self, _name: str) -> object:
        self.calls.append("DispatchEx")
        if self.dispatch_ex_error:
            raise self.dispatch_ex_error
        return self.dispatch_ex_app

    def Dispatch(self, _name: str) -> object:
        self.calls.append("Dispatch")
        if self.dispatch_error:
            raise self.dispatch_error
        return self.dispatch_app


class FakeProjects:
    def __init__(self, project: object) -> None:
        self.project = project
        self.add_calls = []

    def Add(self, DisplayProjectInfo: bool = True, Template: str = "", FileNewDialog: bool = True) -> object:
        self.add_calls.append((DisplayProjectInfo, Template, FileNewDialog))
        return self.project


class FakeNewProjectApp:
    def __init__(self) -> None:
        self.project = object()
        self.ActiveProject = self.project
        self.Projects = FakeProjects(self.project)
        self.view_calls = []

    def ViewApply(self, Name: str = "") -> None:
        self.view_calls.append(Name)


class FakeFailingProjects:
    def Add(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Projects.Add rejected")


class FakeFileNewOnlyApp:
    def __init__(self) -> None:
        self.project = object()
        self.ActiveProject = None
        self.Projects = FakeFailingProjects()
        self.file_new_calls = []
        self.view_calls = []

    def FileNew(
        self,
        SummaryInfo: bool = True,
        Template: str = "",
        FileNewDialog: bool = True,
        FileNewWorkpane: bool = True,
    ) -> None:
        self.file_new_calls.append((SummaryInfo, Template, FileNewDialog, FileNewWorkpane))
        self.ActiveProject = self.project

    def ViewApply(self, Name: str = "") -> None:
        self.view_calls.append(Name)


class FakeVisibilityRejectingApp:
    def __init__(self) -> None:
        self.display_alerts = True

    @property
    def Visible(self) -> bool:
        return False

    @Visible.setter
    def Visible(self, _value: bool) -> None:
        raise AttributeError("Property 'MSProject.Application.Visible' can not be set")

    @property
    def DisplayAlerts(self) -> bool:
        return self.display_alerts

    @DisplayAlerts.setter
    def DisplayAlerts(self, value: bool) -> None:
        self.display_alerts = value


class FakeCell:
    def __init__(self) -> None:
        self.CellColorEx = 0


class FakeFormattingApp:
    def __init__(self) -> None:
        self.ActiveCell = FakeCell()
        self.select_calls = []

    def SelectTaskCell(self, Row: int, Column: str, RowRelative: bool) -> None:
        self.select_calls.append(("cell", Row, Column, RowRelative))

    def SelectTaskField(self, Row: int, Column: str, RowRelative: bool) -> None:
        self.select_calls.append(("field", Row, Column, RowRelative))


class FakeRejectingCell:
    @property
    def CellColorEx(self) -> int:
        return 0

    @CellColorEx.setter
    def CellColorEx(self, _value: int) -> None:
        raise AttributeError("CellColorEx cannot be set")


class FakeFontFallbackFormattingApp(FakeFormattingApp):
    def __init__(self) -> None:
        super().__init__()
        self.ActiveCell = FakeRejectingCell()
        self.font32_colors = []

    def Font32Ex(
        self,
        _name: object,
        _size: object,
        _bold: object,
        _italic: object,
        _underline: object,
        _color: object,
        _reset: object,
        cell_color: int,
        _pattern: object,
        _strikethrough: object,
    ) -> None:
        self.font32_colors.append(cell_color)


class FakeAssignment:
    def __init__(self, resource_id: int) -> None:
        self.ResourceID = resource_id


class FakeAssignments:
    def __init__(self) -> None:
        self.items = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def __call__(self, index: int) -> FakeAssignment:
        return self.items[index - 1]

    def Add(self, ResourceID: int) -> FakeAssignment:
        assignment = FakeAssignment(ResourceID)
        self.items.append(assignment)
        return assignment


class FakeResource:
    def __init__(self, resource_id: int, name: str) -> None:
        self.ID = resource_id
        self.Name = name
        self.Group = ""


class FakeResources:
    def __init__(self) -> None:
        self.items = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def __call__(self, index: int) -> FakeResource:
        return self.items[index - 1]

    def Add(self, name: str) -> FakeResource:
        resource = FakeResource(len(self.items) + 1, name)
        self.items.append(resource)
        return resource


class FakeProject:
    def __init__(self) -> None:
        self.Resources = FakeResources()


class FakeTask:
    def __init__(self) -> None:
        self.Assignments = FakeAssignments()
        self.ResourceNames = ""


if __name__ == "__main__":
    unittest.main()
