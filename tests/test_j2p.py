from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Dict, Tuple

from j2p.cli import build_parser, main
from j2p.config import ConfigError, load_config
from j2p.core import (
    AuditItem,
    J2PError,
    PlanEpic,
    RunPlan,
    ProjectTaskSnapshot,
    build_run_plan,
    calculate_story_point_ratio,
    parse_logged_hours,
    run_plan_to_state,
    snapshots_from_state,
    write_json,
)
from j2p.project import (
    MicrosoftProjectSession,
    ProjectAutomationError,
    append_resource_name,
    cascade_branch_driver_keys,
    project_column_for_audit_field,
    project_column_aliases,
    project_date_for_com,
    project_pj_color,
    project_predecessor_ids,
    review_table_columns,
)
from j2p.reports import (
    project_wide_story_point_ratio_summary,
    render_schedule_cascade_review,
    resource_group_story_point_ratio_rows,
    write_reports,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURES = ROOT / "tests" / "fixtures"


def predecessor_coverage_ratio(plan: RunPlan) -> float:
    driving_epics = [epic for epic in plan.epics.values() if epic.drives_schedule]
    if not driving_epics:
        return 0.0
    with_predecessors = [epic for epic in driving_epics if epic.predecessors]
    return len(with_predecessors) / len(driving_epics)


class J2PPlanningTests(unittest.TestCase):
    def test_initiative_mode_rollups_dependencies_and_exclusions(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
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
        plan = build_run_plan(FIXTURES / "project-wide-jira-update.csv", config, baseline)

        self.assertEqual(plan.rollup_mode, "mixed")
        self.assertIn("TEAM-101", plan.epics)
        self.assertEqual(plan.epics["TEAM-101"].percent_complete, 100)
        self.assertEqual(plan.epics["TEAM-101"].logged_hours, 14)
        self.assertEqual(plan.epics["TEAM-101"].completed_logged_hours, 14)
        self.assertEqual(plan.epics["TEAM-101"].story_point_ratio, 7.43)
        self.assertEqual(plan.epics["TEAM-102"].percent_complete, 38)
        self.assertEqual(plan.epics["TEAM-102"].logged_hours, 7.25)
        self.assertEqual(plan.epics["TEAM-102"].completed_logged_hours, 3.25)
        self.assertEqual(plan.epics["TEAM-102"].story_point_ratio, 7.38)
        self.assertEqual(plan.epics["TEAM-103"].rollup_key, "PLAT-100")
        self.assertEqual(plan.epics["PLAT-201"].rollup_mode, "fixVersion")
        self.assertEqual(plan.epics["PLAT-201"].rollup_key, "Portal 2026")
        self.assertEqual(plan.epics["PLAT-201"].logged_hours, 10)
        self.assertEqual(plan.epics["PLAT-201"].story_point_ratio, 6.4)
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

    def test_parse_logged_hours_accepts_common_jira_time_formats(self) -> None:
        self.assertEqual(parse_logged_hours("1.5"), 1.5)
        self.assertEqual(parse_logged_hours("1h 30m"), 1.5)
        self.assertEqual(parse_logged_hours("1:15"), 1.25)
        self.assertEqual(parse_logged_hours("1d 2h"), 10)
        self.assertEqual(parse_logged_hours("45m"), 0.75)
        self.assertEqual(parse_logged_hours(""), 0)

    def test_story_point_ratio_uses_configured_story_point_hours(self) -> None:
        self.assertEqual(calculate_story_point_ratio(40, 5, 8), 1)
        self.assertEqual(calculate_story_point_ratio(30, 5, 8), 1.33)
        self.assertEqual(calculate_story_point_ratio(45, 5, 8), 0.89)
        self.assertEqual(calculate_story_point_ratio(0, 0, 8), 0)

    def test_story_point_ratio_plan_uses_configured_story_point_hours(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Logged Hours,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "PROD-1,1,Initiative,Program Alpha,,,,,,In Progress,,,,,",
                "TEAM-1,2,Epic,First epic,,PROD-1,,,,In Progress,,2026-01-01,2026-01-15,,",
                "TEAM-11,4,Story,First story,TEAM-1,,,5,40,Done,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "story-point-ratio.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(
                FIXTURES / "mixed-config.yaml",
                {"metrics": {"hours_per_story_point": 10}},
            )
            plan = build_run_plan(csv_path, config)

        self.assertEqual(plan.epics["TEAM-1"].story_point_ratio, 1.25)
        self.assertEqual(plan.stats["story_point_ratio"], 1.25)

    def test_unparsed_logged_hours_are_reported_without_stopping_run(self) -> None:
        csv_text = "\n".join(
            [
                "Issue key,Issue id,Issue Type,Summary,Epic Link,Parent,Fix versions,Story Points,Logged Hours,Status,Resolution,Target start,Target end,Outward issue link (Blocks),Inward issue link (Blocks)",
                "PROD-1,1,Initiative,Program Alpha,,,,,,In Progress,,,,,",
                "TEAM-1,2,Epic,First epic,,PROD-1,,,,In Progress,,2026-01-01,2026-01-15,,",
                "TEAM-11,4,Story,First story,TEAM-1,,,3,about a day,Done,,,,,",
            ]
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "bad-hours.csv"
            csv_path.write_text(csv_text, encoding="utf-8")
            config = load_config(FIXTURES / "mixed-config.yaml")
            plan = build_run_plan(csv_path, config)

        self.assertEqual(plan.epics["TEAM-1"].logged_hours, 0)
        self.assertIn("UnparsedLoggedHours", {item.category for item in plan.audit_items})

    def test_fixversion_mode_defaults_multi_fixversion_epics_to_reference_rows(self) -> None:
        config = load_config(FIXTURES / "fixversion-config.yaml")
        plan = build_run_plan(FIXTURES / "project-wide-jira-fixversion.csv", config)

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
            config = load_config(FIXTURES / "fixversion-config.yaml")
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
            config = load_config(FIXTURES / "mixed-config.yaml")
            plan = build_run_plan(csv_path, config)

        reference_summary = plan.summaries["fixVersion:Shop Drop A"]
        self.assertEqual(reference_summary.total_story_points, 0)
        self.assertEqual(reference_summary.completed_story_points, 0)
        self.assertEqual(reference_summary.reference_epic_count, 1)
        self.assertEqual(reference_summary.percent_complete, 38)

    def test_old_multiple_fixversion_behavior_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "multiple_fix_versions is no longer supported"):
            load_config(None, {"behavior": {"multiple_fix_versions": "exclude"}})

    def test_top_level_rollup_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Top-level rollup_mode is no longer supported"):
            load_config(None, {"rollup_mode": "initiative"})

    def test_resource_groups_require_prefix_rollup_modes(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Missing: TEAM"):
            load_config(
                None,
                {
                    "resource_groups": {"TEAM": "Product Delivery"},
                    "rollup_modes": {},
                },
            )

    def test_rollup_modes_must_match_resource_groups(self) -> None:
        with self.assertRaisesRegex(ConfigError, "not in resource_groups.*OPS"):
            load_config(
                None,
                {
                    "resource_groups": {"TEAM": "Product Delivery"},
                    "rollup_modes": {"TEAM": "initiative", "OPS": "fixVersion"},
                },
            )

    def test_multi_fixversion_policy_normalizes_case_and_whitespace(self) -> None:
        config = load_config(None, {"multi_fixversion_policy": {"default": " Reference ", "ops": "SPLIT"}})

        self.assertEqual(config["multi_fixversion_policy"]["default"], "reference")
        self.assertEqual(config["multi_fixversion_policy"]["OPS"], "split")

    def test_review_table_exposed_columns_rejects_scalar_other_than_all(self) -> None:
        with self.assertRaisesRegex(ConfigError, "review_table.exposed_columns"):
            load_config(None, {"review_table": {"exposed_columns": "finish"}})

    def test_validate_cli_writes_manager_and_audit_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            buffer = StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "validate",
                        "--jira-csv",
                        str(FIXTURES / "project-wide-jira-update.csv"),
                        "--config",
                        str(FIXTURES / "mixed-config.yaml"),
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
            manager_report = run_dir / "html-report" / "Manager-Review-Report.html"
            self.assertTrue(manager_report.exists())
            self.assertTrue((run_dir / "html-report" / "index.html").exists())
            self.assertTrue(
                (run_dir / "html-report" / "resource-groups" / "Product_Delivery.html").exists()
            )
            self.assertTrue((run_dir / "audit-detail.csv").exists())
            self.assertTrue((run_dir / "planned-epics.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "TEAM" / "audit-detail.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "TEAM" / "planned-epics.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "PLAT" / "summary-rollups.csv").exists())
            self.assertTrue((run_dir / "by-project-key" / "UNK" / "audit-detail.csv").exists())
            report = manager_report.read_text(encoding="utf-8")
            self.assertIn("Reviewer Action Needed", report)
            self.assertIn("Decision Briefing", report)
            self.assertIn("Rollup Status", report)
            self.assertIn("Review Type Summary", report)
            self.assertIn("Report Context", report)
            self.assertIn("Logged Hours", report)
            self.assertIn("Story Point Ratio", report)
            self.assertIn("Color Key", report)
            self.assertIn("Color Case Examples", report)
            self.assertIn("Project Key Rollup Mapping", report)
            self.assertIn("<details class=\"detail-block\">", report)
            self.assertIn("Full Planned Epic Rows", report)
            self.assertLess(report.index("Rollup Status"), report.index("Reviewer Action Needed"))
            self.assertLess(report.index("Reviewer Action Needed"), report.index("Full Planned Epic Rows"))
            self.assertIn("Unknown team epic", report)
            resource_report = (
                run_dir / "html-report" / "resource-groups" / "Product_Delivery.html"
            ).read_text(encoding="utf-8")
            self.assertIn("Scope: Resource Group: Product Delivery", resource_report)
            self.assertNotIn("Unknown team epic", resource_report)
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
            config = load_config(FIXTURES / "mixed-config.yaml")
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
            config = load_config(FIXTURES / "mixed-config.yaml")
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
            config = load_config(FIXTURES / "mixed-config.yaml")
            plan = build_run_plan(csv_path, config)
            self.assertEqual(plan.epics["TEAM-1"].total_story_points, 0)
            categories = {item.category for item in plan.audit_items}
            self.assertIn("StoryMissingEpicLink", categories)
            self.assertIn("InPlanning", categories)

    def test_manager_report_is_self_contained_and_field_mapping_includes_status(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        plan = build_run_plan(FIXTURES / "project-wide-jira-update.csv", config)
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            legacy_report = run_dir / "Manager-Review-Report.html"
            legacy_report.write_text("old report", encoding="utf-8")
            paths = write_reports(plan, run_dir, config)
            report = paths["manager_report"].read_text(encoding="utf-8")
            self.assertFalse(legacy_report.exists())
            self.assertTrue((run_dir / "html-report" / "index.html").exists())
            self.assertTrue((run_dir / "html-report" / "resource-groups").exists())
            self.assertNotIn("<script src=", report)
            self.assertNotIn("<link rel=", report)
            self.assertNotIn("https://", report)
            self.assertIn("Story Point Ratio", report)
            self.assertIn("Story Point Ratio By Resource Group", report)
            self.assertIn("Resource Group Story Point Ratio", report)
            field_mapping = paths["field_mapping"].read_text(encoding="utf-8")
            self.assertIn("Native Project fields used by j2p", field_mapping)
            self.assertIn("| Resource Group | Resource Group |", field_mapping)
            self.assertIn("Jira Status", field_mapping)
            self.assertIn("Logged Hours", field_mapping)
            self.assertNotIn("| `resource_group` | `Text6` |", field_mapping)

    def test_manager_story_point_ratio_breakdown_uses_in_progress_scheduled_epics_only(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        plan = build_run_plan(FIXTURES / "project-wide-jira-update.csv", config)

        project_ratio = project_wide_story_point_ratio_summary(plan)
        self.assertEqual(project_ratio["epic_count"], 1)
        self.assertEqual(project_ratio["total_story_points"], 8)
        self.assertEqual(project_ratio["completed_story_points"], 3)
        self.assertEqual(project_ratio["logged_hours"], 7.25)
        self.assertEqual(project_ratio["completed_logged_hours"], 3.25)
        self.assertEqual(project_ratio["expected_completed_hours"], 24)
        self.assertEqual(project_ratio["story_point_ratio"], 7.38)

        resource_rows = resource_group_story_point_ratio_rows(plan)
        self.assertEqual(len(resource_rows), 1)
        self.assertEqual(resource_rows[0]["resource_group"], "Product Delivery")
        self.assertEqual(resource_rows[0]["project_keys"], "TEAM")
        self.assertEqual(resource_rows[0]["story_point_ratio"], 7.38)

    def test_state_round_trip_can_be_used_as_baseline(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        initial = build_run_plan(FIXTURES / "project-wide-jira-initial.csv", config)
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "j2p-state.json"
            state_data = run_plan_to_state(initial)
            first_state_epic = next(iter(state_data["epics"].values()))
            self.assertIn("story_point_ratio", first_state_epic)
            write_json(state_path, state_data)
            baseline = snapshots_from_state(state_path)
            follow_on = build_run_plan(FIXTURES / "project-wide-jira-update.csv", config, baseline)
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
        self.assertGreaterEqual(predecessor_coverage_ratio(baseline), 0.60)
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
        self.assertGreaterEqual(predecessor_coverage_ratio(follow_on), 0.60)

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

    def test_schedule_review_marks_every_changed_branch_driver_red(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")

        def epic(key: str, summary: str, successors: list[str] | None = None) -> PlanEpic:
            return PlanEpic(
                key=key,
                issue_id=key,
                summary=summary,
                status="In Progress",
                rollup_mode="initiative",
                rollup_key="PROD-100",
                rollup_name="Program",
                resource_group="Product Delivery",
                key_prefix="TEAM",
                total_story_points=8,
                completed_story_points=0,
                logged_hours=0,
                completed_logged_hours=0,
                story_point_ratio=0,
                percent_complete=0,
                in_planning=False,
                completed=False,
                target_start="2026-01-01",
                target_end="2026-01-31",
                successors=successors or [],
            )

        plan = RunPlan(
            generated_at="2026-01-01T00:00:00",
            jira_csv="unit.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={
                "TEAM-A": epic("TEAM-A", "Branch driver", ["TEAM-B", "TEAM-C"]),
                "TEAM-B": epic("TEAM-B", "Nested branch driver", ["TEAM-D"]),
                "TEAM-C": epic("TEAM-C", "Downstream leaf"),
                "TEAM-D": epic("TEAM-D", "Nested downstream leaf"),
                "TEAM-E": epic("TEAM-E", "Independent changed item"),
            },
            audit_items=[],
        )
        before = {
            key: ProjectTaskSnapshot(key=key, finish=f"2026-01-{index:02d}")
            for index, key in enumerate(plan.epics, start=1)
        }
        after = {
            key: ProjectTaskSnapshot(key=key, finish=f"2026-02-{index:02d}")
            for index, key in enumerate(plan.epics, start=1)
        }
        session = object.__new__(MicrosoftProjectSession)
        session.snapshot_tasks = lambda _config: after

        MicrosoftProjectSession.add_schedule_review_items(session, plan, before, config)

        self.assertEqual(cascade_branch_driver_keys(plan, set(after)), {"TEAM-A", "TEAM-B"})
        red_keys = {
            item.jira_key
            for item in plan.audit_items
            if item.category == "CascadeBranchDriver" and item.color == "cascade_root"
        }
        green_keys = {
            item.jira_key
            for item in plan.audit_items
            if item.category == "CascadingDateChange" and item.color == "changed_cell"
        }
        self.assertEqual(red_keys, {"TEAM-A", "TEAM-B"})
        self.assertEqual(green_keys, {"TEAM-C", "TEAM-D", "TEAM-E"})

        html = render_schedule_cascade_review(plan, project_update_run=True)
        self.assertIn("Schedule Cascade Review", html)
        self.assertIn("Red Branch Drivers", html)
        self.assertIn("TEAM-A", html)
        self.assertIn("TEAM-B", html)
        self.assertIn("Nested branch driver", html)
        self.assertIn("Changed Downstream Successors", html)

    def test_schedule_cascade_review_collapses_and_sorts_all_branches(self) -> None:
        def epic(key: str, summary: str, successors: list[str] | None = None) -> PlanEpic:
            return PlanEpic(
                key=key,
                issue_id=key,
                summary=summary,
                status="In Progress",
                rollup_mode="initiative",
                rollup_key="PROD-100",
                rollup_name="Program",
                resource_group="Product Delivery",
                key_prefix="TEAM",
                total_story_points=8,
                completed_story_points=0,
                logged_hours=0,
                completed_logged_hours=0,
                story_point_ratio=0,
                percent_complete=0,
                in_planning=False,
                completed=False,
                target_start="2026-01-01",
                target_end="2026-01-31",
                successors=successors or [],
            )

        def schedule_item(key: str, category: str) -> AuditItem:
            return AuditItem(
                severity="info",
                category=category,
                jira_key=key,
                schedule_key=key,
                summary=f"{key} summary",
                old_value="2026-01-01",
                new_value="2026-02-01",
            )

        plan = RunPlan(
            generated_at="2026-01-01T00:00:00",
            jira_csv="unit.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={
                "TEAM-A": epic(
                    "TEAM-A",
                    "Large branch",
                    ["TEAM-B", "TEAM-C", "TEAM-D", "TEAM-E", "TEAM-F", "TEAM-G"],
                ),
                "TEAM-B": epic("TEAM-B", "Affected one"),
                "TEAM-C": epic("TEAM-C", "Affected two"),
                "TEAM-D": epic("TEAM-D", "Affected three"),
                "TEAM-E": epic("TEAM-E", "Affected four"),
                "TEAM-F": epic("TEAM-F", "Affected five"),
                "TEAM-G": epic("TEAM-G", "Affected six"),
                "TEAM-X": epic("TEAM-X", "Small branch", ["TEAM-Y", "TEAM-Z"]),
                "TEAM-Y": epic("TEAM-Y", "Small affected one"),
                "TEAM-Z": epic("TEAM-Z", "Small affected two"),
            },
            audit_items=[
                schedule_item("TEAM-A", "CascadeBranchDriver"),
                schedule_item("TEAM-B", "CascadingDateChange"),
                schedule_item("TEAM-C", "CascadingDateChange"),
                schedule_item("TEAM-D", "CascadingDateChange"),
                schedule_item("TEAM-E", "CascadingDateChange"),
                schedule_item("TEAM-F", "CascadingDateChange"),
                schedule_item("TEAM-G", "CascadingDateChange"),
                schedule_item("TEAM-X", "CascadeBranchDriver"),
                schedule_item("TEAM-Y", "CascadingDateChange"),
                schedule_item("TEAM-Z", "CascadingDateChange"),
            ],
        )

        html = render_schedule_cascade_review(plan, project_update_run=True)

        self.assertEqual(html.count('<details class="cascade-branch">'), 2)
        self.assertIn("6 downstream affected issues", html)
        self.assertLess(html.index("TEAM-A: TEAM-A summary"), html.index("TEAM-X"))

    def test_schedule_cascade_review_filters_branch_roots_by_resource_group(self) -> None:
        def epic(key: str, summary: str, resource_group: str, successors: list[str] | None = None) -> PlanEpic:
            return PlanEpic(
                key=key,
                issue_id=key,
                summary=summary,
                status="In Progress",
                rollup_mode="initiative",
                rollup_key="PROD-100",
                rollup_name="Program",
                resource_group=resource_group,
                key_prefix=key.split("-", 1)[0],
                total_story_points=8,
                completed_story_points=0,
                logged_hours=0,
                completed_logged_hours=0,
                story_point_ratio=0,
                percent_complete=0,
                in_planning=False,
                completed=False,
                target_start="2026-01-01",
                target_end="2026-01-31",
                successors=successors or [],
            )

        def schedule_item(key: str, category: str) -> AuditItem:
            return AuditItem(
                severity="info",
                category=category,
                jira_key=key,
                schedule_key=key,
                summary=f"{key} summary",
                old_value="2026-01-01",
                new_value="2026-02-01",
            )

        plan = RunPlan(
            generated_at="2026-01-01T00:00:00",
            jira_csv="unit.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={
                "TEAM-A": epic("TEAM-A", "Alpha branch", "Alpha", ["TEAM-B"]),
                "TEAM-B": epic("TEAM-B", "Beta downstream", "Beta", ["TEAM-C"]),
                "TEAM-C": epic("TEAM-C", "Alpha downstream", "Alpha"),
                "TEAM-X": epic("TEAM-X", "Beta branch", "Beta", ["TEAM-Y"]),
                "TEAM-Y": epic("TEAM-Y", "Alpha downstream from beta", "Alpha"),
            },
            audit_items=[
                schedule_item("TEAM-A", "CascadeBranchDriver"),
                schedule_item("TEAM-B", "CascadeBranchDriver"),
                schedule_item("TEAM-C", "CascadingDateChange"),
                schedule_item("TEAM-X", "CascadeBranchDriver"),
                schedule_item("TEAM-Y", "CascadingDateChange"),
            ],
        )

        html = render_schedule_cascade_review(
            plan,
            project_update_run=True,
            root_resource_group="Alpha",
        )

        self.assertIn("TEAM-A: TEAM-A summary", html)
        self.assertIn("TEAM-B", html)
        self.assertIn("TEAM-C", html)
        self.assertNotIn("TEAM-X", html)
        self.assertNotIn("TEAM-Y", html)

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
        config = load_config(FIXTURES / "mixed-config.yaml")

        self.assertEqual(project_column_for_audit_field("Rollup Key", config), "Text5")
        self.assertEqual(project_column_for_audit_field("Jira Target End", config), "Date2")
        self.assertEqual(project_column_for_audit_field("Resource Group", config), "Resource Group")
        self.assertEqual(project_column_for_audit_field("Logged Hours", config), "Number3")
        self.assertEqual(project_column_for_audit_field("Story Point Ratio", config), "Number4")
        self.assertEqual(project_column_for_audit_field("Status", config), "Text9")
        self.assertEqual(project_column_for_audit_field("Unmatched Project Task", config), "Flag2")
        self.assertEqual(project_column_aliases("Text1", config), ["Text1", "Jira Key"])
        self.assertEqual(project_column_aliases("Name", config), ["Name"])

    def test_review_table_columns_use_manager_friendly_defaults(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")

        columns = review_table_columns(config, ["Date2", "Number3", "Flag2"])

        self.assertEqual(
            columns,
            [
                "Name",
                "Text1",
                "Resource Group",
                "Text8",
                "Text9",
                "Start",
                "Finish",
                "% Complete",
                "Predecessors",
            ],
        )
        self.assertNotIn("Text10", columns)
        self.assertNotIn("Text4", columns)
        self.assertNotIn("Text5", columns)
        self.assertNotIn("Text7", columns)
        self.assertNotIn("Text3", columns)
        self.assertNotIn("Date1", columns)
        self.assertNotIn("Date2", columns)
        self.assertNotIn("Number1", columns)
        self.assertNotIn("Number2", columns)
        self.assertNotIn("Number3", columns)
        self.assertNotIn("Number4", columns)
        self.assertNotIn("Flag1", columns)
        self.assertNotIn("Flag2", columns)
        self.assertNotIn("Flag3", columns)
        self.assertNotIn("Flag4", columns)
        self.assertNotIn("Text11", columns)
        self.assertNotIn("Text12", columns)

    def test_review_table_columns_can_be_pruned_by_config(self) -> None:
        config = load_config(
            FIXTURES / "mixed-config.yaml",
            {
                "review_table": {
                    "exposed_columns": ["jira_key", "finish", "logged_hours"],
                }
            },
        )

        columns = review_table_columns(config, ["Flag2"])

        self.assertEqual(columns, ["Name", "Text1", "Finish", "Number3"])

    def test_review_table_columns_can_include_audit_columns_by_config(self) -> None:
        config = load_config(
            None,
            {
                "review_table": {
                    "exposed_columns": ["jira_key", "finish"],
                    "include_audit_columns": True,
                }
            },
        )

        columns = review_table_columns(config, ["Flag2", "Date2"])

        self.assertEqual(columns, ["Name", "Text1", "Finish", "Flag2", "Date2"])

    def test_review_table_columns_can_hide_audit_columns(self) -> None:
        config = load_config(
            None,
            {
                "review_table": {
                    "exposed_columns": ["Jira Key", "Number3"],
                    "include_audit_columns": False,
                }
            },
        )

        columns = review_table_columns(config, ["Flag2", "Predecessors"])

        self.assertEqual(columns, ["Name", "Text1", "Number3"])

    def test_prepare_formatting_view_creates_and_applies_review_table(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeReviewTableApp()

        errors = MicrosoftProjectSession.prepare_formatting_view(
            session,
            ["Text1", "Text9", "Predecessors"],
            config,
        )

        self.assertEqual(errors, [])
        self.assertEqual(session.app.table_apply_calls, ["j2p Review"])
        added_columns = [call["NewFieldName"] for call in session.app.table_edit_calls if call.get("NewFieldName")]
        self.assertIn("Text1", added_columns)
        self.assertIn("Text9", added_columns)
        self.assertNotIn("Predecessors", added_columns)

    def test_apply_review_formatting_skips_hidden_default_columns(self) -> None:
        config = load_config(None)
        task = FakeTask()
        task.ID = 15
        plan = RunPlan(
            generated_at="now",
            jira_csv="jira.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={},
            audit_items=[
                AuditItem("Info", "ChangedField", jira_key="CORE-1", field="Jira Target End", color="changed_cell"),
                AuditItem("Review", "RollupMove", jira_key="CORE-1", field="Rollup Key", color="changed_cell"),
                AuditItem("Info", "ChangedField", jira_key="CORE-1", field="Finish", color="changed_cell"),
            ],
        )
        prepared_columns: List[str] = []
        colored_columns: List[str] = []
        session = object.__new__(MicrosoftProjectSession)
        session.index_tasks_by_key = lambda _config: {"CORE-1": task}
        session.project_selection_aliases = lambda column, _config: [column]
        session.prepare_formatting_view = lambda columns, _config: prepared_columns.extend(columns) or []
        session.project_table_column_positions = lambda _table, _config: {
            column.lower(): index for index, column in enumerate(prepared_columns, start=1)
        }
        session.color_project_cell_error = lambda _task, column, _color, _aliases, _position: (
            colored_columns.append(column) or ""
        )

        MicrosoftProjectSession.apply_review_formatting(session, plan, config)

        self.assertNotIn("Date2", prepared_columns)
        self.assertNotIn("Text5", prepared_columns)
        self.assertIn("Finish", prepared_columns)
        self.assertEqual(colored_columns, ["Finish"])

    def test_prepare_formatting_view_updates_existing_review_table_on_name_conflict(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeExistingReviewTableApp()

        errors = MicrosoftProjectSession.prepare_formatting_view(
            session,
            ["Name", "Text1", "Text9", "Predecessors"],
            config,
        )

        self.assertEqual(errors, [])
        self.assertEqual(session.app.table_apply_calls[-1], "j2p Review")
        create_calls = [call for call in session.app.table_edit_calls if call.get("Create")]
        self.assertEqual(len(create_calls), 1)
        added_columns = [call["NewFieldName"] for call in session.app.table_edit_calls if call.get("NewFieldName")]
        self.assertIn("Text1", added_columns)
        self.assertIn("Text9", added_columns)
        self.assertIn("Predecessors", added_columns)

    def test_create_review_table_prefers_project_table_fields_object_model(self) -> None:
        config = load_config(FIXTURES / "mixed-config.yaml")
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeObjectModelReviewTableApp()
        session.project = session.app.project

        errors = MicrosoftProjectSession.prepare_formatting_view(
            session,
            ["Name", "Text1", "Text9", "Predecessors"],
            config,
        )

        self.assertEqual(errors, [])
        self.assertEqual(session.app.table_edit_calls, [])
        self.assertEqual(session.app.table_apply_calls[-1], "j2p Review")
        table = session.project.TaskTables("j2p Review")
        self.assertEqual(table.fields, ["Name", "Text1", "Text9", "Predecessors"])

    def test_color_project_cell_sets_active_cell_background(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeFormattingApp()
        task = FakeTask()
        task.ID = 7

        self.assertTrue(MicrosoftProjectSession.color_project_cell(session, task, "Text1", "#C6EFCE"))
        self.assertEqual(session.app.select_calls, [("field", 7, "Text1", False)])
        self.assertNotEqual(session.app.ActiveCell.CellColorEx, 0)

    def test_color_project_cell_does_not_call_font32ex_when_property_write_works(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeUnexpectedFont32ExApp()
        task = FakeTask()
        task.ID = 8

        self.assertTrue(MicrosoftProjectSession.color_project_cell(session, task, "Text2", "#FFC7CE"))
        self.assertEqual(session.app.font32_calls, 0)

    def test_project_adapter_does_not_call_font32ex(self) -> None:
        source = (ROOT / "j2p" / "project.py").read_text(encoding="utf-8")

        self.assertNotIn("Font32Ex", source)

    def test_color_project_cell_uses_cellcolor_fallback_without_font32ex(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeCellColorFallbackFormattingApp()
        task = FakeTask()
        task.ID = 9

        self.assertTrue(MicrosoftProjectSession.color_project_cell(session, task, "Text2", "#FFC7CE"))
        self.assertEqual(session.app.font32_calls, 0)
        self.assertEqual(session.app.ActiveCell.CellColor, 1)
        self.assertEqual(session.app.ActiveCell.Pattern, 1)

    def test_project_pj_color_maps_default_review_colors_to_visible_palette(self) -> None:
        self.assertEqual(project_pj_color("#C6EFCE"), 3)
        self.assertEqual(project_pj_color("#FFC7CE"), 1)
        self.assertEqual(project_pj_color("#FFEB9C"), 2)
        self.assertEqual(project_pj_color("#BDD7EE"), 5)
        self.assertEqual(project_pj_color("#D9EAD3"), 15)

    def test_select_project_cell_treats_false_return_as_failure(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeFalseThenTrueSelectionApp()
        task = FakeTask()
        task.ID = 9

        selected, error = MicrosoftProjectSession.select_project_cell(session, task, ["Text2"])

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertEqual(
            session.app.select_calls,
            [("field", 9, "Text2", False)],
        )

    def test_select_project_cell_tries_column_aliases(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeAliasSelectionApp("Jira Key")
        task = FakeTask()
        task.ID = 10

        selected, error = MicrosoftProjectSession.select_project_cell(
            session,
            task,
            ["Text1", "Jira Key"],
        )

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertIn(("field", 10, "Text1", False), session.app.select_calls)
        self.assertIn(("cell", 10, "Text1", False), session.app.select_calls)
        self.assertEqual(session.app.select_calls[-1], ("field", 10, "Jira Key", False))

    def test_select_project_cell_falls_back_to_numeric_column_position(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeSelectCellFallbackApp("Text9")
        task = FakeTask()
        task.ID = 11

        selected, error = MicrosoftProjectSession.select_project_cell(
            session,
            task,
            ["Text9", "Status"],
            column_position=6,
        )

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertEqual(session.app.select_cell_calls, [("cell", 11, 6, False)])
        self.assertEqual(session.app.select_task_cell_calls, [])

    def test_select_project_cell_recovers_from_wrong_numeric_column_position(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeSelectCellFallbackApp("Finish")
        task = FakeTask()
        task.ID = 12

        selected, error = MicrosoftProjectSession.select_project_cell(
            session,
            task,
            ["Text9", "Status"],
            column_position=6,
        )

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertIn(("cell", 12, 6, False), session.app.select_cell_calls)
        self.assertEqual(session.app.select_task_cell_calls[-1], ("field", 12, "Text9", False))

    def test_select_project_cell_tries_offset_select_range_column(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeOffsetSelectRangeApp(target_column=7, selected_field_name="Text9")
        task = FakeTask()
        task.ID = 13

        selected, error = MicrosoftProjectSession.select_project_cell(
            session,
            task,
            ["Text9", "Status"],
            column_position=6,
        )

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertIn(("range", 13, 7, False, 0, 0, False, False), session.app.select_range_calls)

    def test_select_project_cell_uses_extended_task_field_args(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        session.app = FakeExtendedTaskFieldApp()
        task = FakeTask()
        task.ID = 14

        selected, error = MicrosoftProjectSession.select_project_cell(session, task, ["Finish"])

        self.assertTrue(selected)
        self.assertEqual(error, "")
        self.assertIn(("field-extended", 14, "Finish", False, 0, 0, False, False), session.app.select_calls)

    def test_write_project_predecessors_uses_fs_ids_and_verifies_readback(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        task = FakeTask()

        error = MicrosoftProjectSession.write_project_predecessors(session, task, "3FS,4FS", ["3", "4"])

        self.assertEqual(error, "")
        self.assertEqual(task.Predecessors, "3FS,4FS")
        self.assertEqual(project_predecessor_ids("3FS,4SS+2d"), ["3", "4"])

    def test_write_project_predecessors_fast_skips_unchanged_links(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        predecessor = FakeLinkableTask(3)
        task = FakeLinkableTask(7)
        task._predecessors = "3FS"

        error = MicrosoftProjectSession.write_project_predecessors(
            session,
            task,
            "3",
            ["3"],
            [predecessor],
        )

        self.assertEqual(error, "")
        self.assertEqual(task.text_writes, [])
        self.assertEqual(task.link_calls, [])
        self.assertEqual(task.Predecessors, "3FS")

    def test_write_project_predecessors_prefers_project_object_links(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        predecessor = FakeLinkableTask(3)
        task = FakeLinkableTask(7)

        error = MicrosoftProjectSession.write_project_predecessors(
            session,
            task,
            "3FS",
            ["3"],
            [predecessor],
            "diagnostic",
        )

        self.assertEqual(error, "")
        self.assertEqual(task.link_calls, [3])
        self.assertTrue(task.text_writes)
        self.assertEqual(set(task.text_writes), {""})
        self.assertEqual(task.Predecessors, "3FS")

    def test_write_project_predecessors_uses_task_dependencies_collection_first(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        predecessor = FakeLinkableTask(3)
        task = FakeTaskDependencyCollectionTask(7)

        error = MicrosoftProjectSession.write_project_predecessors(
            session,
            task,
            "3",
            ["3"],
            [predecessor],
            "diagnostic",
        )

        self.assertEqual(error, "")
        self.assertEqual(task.TaskDependencies.add_calls, [3])
        self.assertEqual(task.link_calls, [])
        self.assertEqual(set(task.text_writes), {""})
        self.assertEqual(task.Predecessors, "3FS")

    def test_apply_dependencies_links_project_task_objects(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        predecessor = FakeLinkableTask(3)
        task = FakeLinkableTask(7)
        plan = RunPlan(
            generated_at="2026-01-01T00:00:00",
            jira_csv="unit.csv",
            rollup_mode="initiative",
            column_map={},
            stats={},
            summaries={},
            epics={
                "TEAM-1": PlanEpic(
                    key="TEAM-1",
                    issue_id="1",
                    summary="Predecessor",
                    status="In Progress",
                    rollup_mode="initiative",
                    rollup_key="PROD-1",
                    rollup_name="Program",
                    resource_group="Product Delivery",
                    key_prefix="TEAM",
                    total_story_points=1,
                    completed_story_points=0,
                    logged_hours=0,
                    completed_logged_hours=0,
                    story_point_ratio=0,
                    percent_complete=0,
                    in_planning=False,
                    completed=False,
                    target_start="",
                    target_end="",
                ),
                "TEAM-2": PlanEpic(
                    key="TEAM-2",
                    issue_id="2",
                    summary="Successor",
                    status="In Progress",
                    rollup_mode="initiative",
                    rollup_key="PROD-1",
                    rollup_name="Program",
                    resource_group="Product Delivery",
                    key_prefix="TEAM",
                    total_story_points=1,
                    completed_story_points=0,
                    logged_hours=0,
                    completed_logged_hours=0,
                    story_point_ratio=0,
                    percent_complete=0,
                    in_planning=False,
                    completed=False,
                    target_start="",
                    target_end="",
                    predecessors=["TEAM-1"],
                ),
            },
            audit_items=[],
        )

        MicrosoftProjectSession.apply_dependencies(
            session,
            plan,
            {
                "TEAM-1": predecessor,
                "TEAM-2": task,
            },
            "diagnostic",
        )

        self.assertEqual(plan.audit_items, [])
        self.assertEqual(task.link_calls, [3])
        self.assertEqual(task.Predecessors, "3FS")

    def test_write_project_predecessors_reports_stale_extra_ids(self) -> None:
        session = object.__new__(MicrosoftProjectSession)
        task = FakeStalePredecessorTask()

        error = MicrosoftProjectSession.write_project_predecessors(session, task, "3FS", ["3"])

        self.assertIn("unexpected predecessor ID(s) 4", error)


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


class FakeReviewTableApp:
    def __init__(self) -> None:
        self.table_edit_calls = []
        self.table_apply_calls = []

    def ViewApply(self, Name: str = "") -> None:
        pass

    def FilterClear(self) -> None:
        pass

    def GroupApply(self, Name: str = "") -> None:
        pass

    def OutlineShowAllTasks(self) -> None:
        pass

    def TableEditEx(self, **kwargs: object) -> bool:
        self.table_edit_calls.append(kwargs)
        return True

    def TableApply(self, Name: str = "") -> bool:
        self.table_apply_calls.append(Name)
        return True


class FakeTableField:
    def __init__(self, field: str, title: str = "") -> None:
        self.Field = field
        self.Title = title


class FakeTableFields:
    def __init__(self, fields: list[str]) -> None:
        self.fields = fields

    @property
    def Count(self) -> int:
        return len(self.fields)

    def __call__(self, index: int) -> FakeTableField:
        return FakeTableField(self.fields[index - 1])

    def Add(self, Field: object, *args: object, **kwargs: object) -> FakeTableField:
        field = str(Field)
        self.fields.append(field)
        return FakeTableField(field)


class FakeProjectTable:
    def __init__(self, fields: list[str], tables: Optional["FakeTaskTables"] = None, name: str = "j2p Review") -> None:
        self.fields = fields
        self.TableFields = FakeTableFields(self.fields)
        self.tables = tables
        self.name = name

    def Delete(self) -> None:
        if self.tables:
            self.tables.tables.pop(self.name, None)


class FakeTaskTables:
    def __init__(self, tables: dict[str, FakeProjectTable]) -> None:
        self.tables = tables
        for name, table in self.tables.items():
            table.tables = self
            table.name = name

    def __call__(self, name: str) -> FakeProjectTable:
        return self.tables[name]

    def Item(self, name: str) -> FakeProjectTable:
        return self.tables[name]

    def Add(self, Name: str, Field: object, Task: bool) -> FakeProjectTable:
        table = FakeProjectTable([str(Field)], self, Name)
        self.tables[Name] = table
        return table


class FakeProjectWithTables:
    def __init__(self, fields: list[str]) -> None:
        self.TaskTables = FakeTaskTables({"j2p Review": FakeProjectTable(fields)})


class FakeExistingReviewTableApp(FakeReviewTableApp):
    def __init__(self) -> None:
        super().__init__()

    def TableEditEx(self, **kwargs: object) -> bool:
        self.table_edit_calls.append(kwargs)
        if kwargs.get("Create"):
            raise RuntimeError("The name j2p Review is already being used.")
        return True

    def FieldConstantToFieldName(self, field: object) -> str:
        return str(field)


class FakeObjectModelReviewTableApp(FakeReviewTableApp):
    def __init__(self) -> None:
        super().__init__()
        self.project = FakeProjectWithTables(["Name", "Text9"])

    def TableEditEx(self, **kwargs: object) -> bool:
        self.table_edit_calls.append(kwargs)
        return True

    def FieldNameToFieldConstant(self, field_name: str) -> str:
        return field_name

    def FieldConstantToFieldName(self, field: object) -> str:
        return str(field)


class FakeCell:
    def __init__(self) -> None:
        self.CellColorEx = 0
        self.Pattern = 0


class FakeNamedCell(FakeCell):
    def __init__(self, field_name: str) -> None:
        super().__init__()
        self.FieldName = field_name


class FakeFormattingApp:
    def __init__(self) -> None:
        self.ActiveCell = FakeCell()
        self.select_calls = []

    def SelectTaskCell(self, Row: int, Column: str, RowRelative: bool) -> None:
        self.select_calls.append(("cell", Row, Column, RowRelative))

    def SelectTaskField(self, Row: int, Column: str, RowRelative: bool) -> None:
        self.select_calls.append(("field", Row, Column, RowRelative))


class FakeFalseThenTrueSelectionApp(FakeFormattingApp):
    def SelectTaskCell(self, Row: int, Column: str, RowRelative: bool) -> bool:
        self.select_calls.append(("cell", Row, Column, RowRelative))
        return 0

    def SelectTaskField(self, Row: int, Column: str, RowRelative: bool) -> bool:
        self.select_calls.append(("field", Row, Column, RowRelative))
        return True


class FakeAliasSelectionApp(FakeFormattingApp):
    def __init__(self, selectable_column: str) -> None:
        super().__init__()
        self.selectable_column = selectable_column

    def SelectTaskCell(self, Row: int, Column: str, RowRelative: bool) -> bool:
        self.select_calls.append(("cell", Row, Column, RowRelative))
        return Column == self.selectable_column

    def SelectTaskField(self, Row: int, Column: str, RowRelative: bool) -> bool:
        self.select_calls.append(("field", Row, Column, RowRelative))
        return Column == self.selectable_column


class FakeSelectCellFallbackApp(FakeFormattingApp):
    def __init__(self, selected_field_name: str) -> None:
        super().__init__()
        self.ActiveCell = FakeNamedCell(selected_field_name)
        self.select_cell_calls = []
        self.select_task_cell_calls = []

    def SelectCell(self, Row: int, Column: int, RowRelative: bool) -> bool:
        self.select_cell_calls.append(("cell", Row, Column, RowRelative))
        return True

    def SelectTaskCell(self, Row: int, Column: str, RowRelative: bool) -> bool:
        self.select_task_cell_calls.append(("cell", Row, Column, RowRelative))
        self.ActiveCell = FakeNamedCell(Column)
        return True

    def SelectTaskField(self, Row: int, Column: str, RowRelative: bool, *args: object) -> bool:
        self.select_task_cell_calls.append(("field", Row, Column, RowRelative))
        self.ActiveCell = FakeNamedCell(Column)
        return True


class FakeOffsetSelectRangeApp(FakeFormattingApp):
    def __init__(self, target_column: int, selected_field_name: str) -> None:
        super().__init__()
        self.ActiveCell = FakeNamedCell("Name")
        self.target_column = target_column
        self.selected_field_name = selected_field_name
        self.select_cell_calls = []
        self.select_range_calls = []

    def SelectCell(self, *args: object, **kwargs: object) -> bool:
        row, column, row_relative = selection_call_parts(args, kwargs)
        self.select_cell_calls.append(("cell", row, column, row_relative))
        return False

    def SelectRange(self, *args: object, **kwargs: object) -> bool:
        row, column, row_relative = selection_call_parts(args, kwargs)
        width = int(kwargs.get("Width", args[3] if len(args) > 3 else 0))
        height = int(kwargs.get("Height", args[4] if len(args) > 4 else 0))
        extend = bool(kwargs.get("Extend", args[5] if len(args) > 5 else False))
        add = bool(kwargs.get("Add", args[6] if len(args) > 6 else False))
        self.select_range_calls.append(("range", row, column, row_relative, width, height, extend, add))
        if column != self.target_column:
            return False
        self.ActiveCell = FakeNamedCell(self.selected_field_name)
        return True


class FakeExtendedTaskFieldApp(FakeFormattingApp):
    def __init__(self) -> None:
        super().__init__()
        self.ActiveCell = FakeNamedCell("Name")

    def SelectTaskField(self, *args: object, **kwargs: object) -> bool:
        row, column, row_relative = selection_call_parts(args, kwargs)
        is_extended = len(args) >= 7 or {"Width", "Height", "Extend", "Add"}.issubset(kwargs)
        if not is_extended:
            self.select_calls.append(("field-basic", row, column, row_relative))
            raise RuntimeError("the argument is not valid")
        width = int(kwargs.get("Width", args[3] if len(args) > 3 else 0))
        height = int(kwargs.get("Height", args[4] if len(args) > 4 else 0))
        extend = bool(kwargs.get("Extend", args[5] if len(args) > 5 else False))
        add = bool(kwargs.get("Add", args[6] if len(args) > 6 else False))
        self.select_calls.append(("field-extended", row, column, row_relative, width, height, extend, add))
        self.ActiveCell = FakeNamedCell(str(column))
        return True

    def SelectTaskCell(self, *args: object, **kwargs: object) -> bool:
        row, column, row_relative = selection_call_parts(args, kwargs)
        self.select_calls.append(("cell", row, column, row_relative))
        raise RuntimeError("the argument is not valid")


def selection_call_parts(args: object, kwargs: Dict[str, object]) -> Tuple[int, object, bool]:
    args_tuple = tuple(args) if isinstance(args, tuple) else tuple()
    row = kwargs.get("Row", args_tuple[0] if len(args_tuple) > 0 else 0)
    column = kwargs.get("Column", args_tuple[1] if len(args_tuple) > 1 else "")
    row_relative = kwargs.get("RowRelative", args_tuple[2] if len(args_tuple) > 2 else False)
    return int(row), column, bool(row_relative)


class FakeRejectingCell:
    def __init__(self) -> None:
        self.CellColor = 0
        self.Pattern = 0

    @property
    def CellColorEx(self) -> int:
        return 0

    @CellColorEx.setter
    def CellColorEx(self, _value: int) -> None:
        raise AttributeError("CellColorEx cannot be set")


class FakeUnexpectedFont32ExApp(FakeFormattingApp):
    def __init__(self) -> None:
        super().__init__()
        self.font32_calls = 0

    def Font32Ex(self, *args: object, **kwargs: object) -> None:
        self.font32_calls += 1
        raise AssertionError("Font32Ex must not be called")


class FakeCellColorFallbackFormattingApp(FakeFormattingApp):
    def __init__(self) -> None:
        super().__init__()
        self.ActiveCell = FakeRejectingCell()
        self.font32_calls = 0

    def Font32Ex(self, *args: object, **kwargs: object) -> None:
        self.font32_calls += 1
        raise AssertionError("Font32Ex must not be called")


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


class FakeLinkableTask(FakeTask):
    def __init__(self, task_id: int) -> None:
        super().__init__()
        self.ID = task_id
        self._predecessors = ""
        self.link_calls = []
        self.text_writes = []

    @property
    def Predecessors(self) -> str:
        return self._predecessors

    @Predecessors.setter
    def Predecessors(self, value: object) -> None:
        self.text_writes.append(str(value))
        self._predecessors = str(value)

    def LinkPredecessors(self, *args: object, **kwargs: object) -> None:
        predecessor = kwargs.get("Tasks") if "Tasks" in kwargs else args[0]
        predecessor_id = int(getattr(predecessor, "ID"))
        self.link_calls.append(predecessor_id)
        current = self._predecessors.strip()
        value = f"{predecessor_id}FS"
        self._predecessors = f"{current},{value}" if current else value


class FakeTaskDependencies:
    def __init__(self, task: FakeLinkableTask) -> None:
        self.task = task
        self.add_calls = []

    def Add(self, predecessor_task: FakeLinkableTask, *_args: object) -> None:
        predecessor_id = int(predecessor_task.ID)
        self.add_calls.append(predecessor_id)
        current = self.task._predecessors.strip()
        value = f"{predecessor_id}FS"
        self.task._predecessors = f"{current},{value}" if current else value


class FakeTaskDependencyCollectionTask(FakeLinkableTask):
    def __init__(self, task_id: int) -> None:
        super().__init__(task_id)
        self.TaskDependencies = FakeTaskDependencies(self)


class FakeStalePredecessorTask(FakeTask):
    def __init__(self) -> None:
        super().__init__()
        self._predecessors = "3FS,4FS"

    @property
    def Predecessors(self) -> str:
        return self._predecessors

    @Predecessors.setter
    def Predecessors(self, _value: object) -> None:
        return


if __name__ == "__main__":
    unittest.main()
