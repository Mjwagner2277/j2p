from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from j2p.cli import main
from j2p.config import load_config
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
from j2p.project import MicrosoftProjectSession
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

    def test_fixversion_mode_uses_fixversion_parent_and_rejects_ambiguous_parent(self) -> None:
        config = load_config(EXAMPLES / "config.fixversion.example.yaml")
        plan = build_run_plan(EXAMPLES / "project-wide-jira-fixversion.csv", config)

        self.assertEqual(plan.rollup_mode, "fixVersion")
        self.assertEqual(plan.epics["TEAM-501"].rollup_key, "Portal 2026")
        self.assertIn("fixVersion:Portal 2026", plan.summaries)
        self.assertNotIn("TEAM-503", plan.epics)
        self.assertIn("ExcludedMissingRollup", {item.category for item in plan.audit_items})

    def test_validate_cli_writes_manager_and_audit_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
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
            self.assertIn("Color Key", report)
            self.assertIn("Color Case Examples", report)
            self.assertIn("Project Key Rollup Mapping", report)
            self.assertIn("Unknown team epic", report)
            audit_header = (run_dir / "audit-detail.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("project_key", audit_header)

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
            self.assertIn("Jira Status", field_mapping)

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


if __name__ == "__main__":
    unittest.main()
