from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from j2p.cli import main
from j2p.config import load_config
from j2p.core import (
    ProjectTaskSnapshot,
    build_run_plan,
    run_plan_to_state,
    snapshots_from_state,
    write_json,
)


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

        self.assertEqual(plan.rollup_mode, "initiative")
        self.assertIn("TEAM-101", plan.epics)
        self.assertEqual(plan.epics["TEAM-101"].percent_complete, 100)
        self.assertEqual(plan.epics["TEAM-102"].percent_complete, 38)
        self.assertEqual(plan.epics["TEAM-103"].rollup_key, "PLAT-100")
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
        self.assertIn("Portal 2026", plan.summaries)
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
            report = (run_dir / "Manager-Review-Report.html").read_text(encoding="utf-8")
            self.assertIn("Reviewer Action Needed", report)
            self.assertIn("Color Key", report)
            self.assertIn("Unknown team epic", report)

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


if __name__ == "__main__":
    unittest.main()
