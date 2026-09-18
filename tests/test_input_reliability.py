"""Regression cases for complete, deterministic Jira input validation."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import ConfigError, load_config, parse_yaml_subset
from j2p.core import build_run_plan
from j2p.jira import CsvTable, parse_logged_hours, parse_number
from j2p.models import J2PError


class InputReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = load_config(None, {
            "resource_groups": {"TEAM": "Team"}, "rollup_modes": {"TEAM": "fixVersion"},
        })
        self.headers = ["Issue key", "Issue Type", "Summary", "Epic Link", "Parent", "Fix versions",
                        "Story Points", "Status", "Logged Hours"]
        self.epic = ["TEAM-1", "Epic", "Epic", "", "", "Release 1", "", "In Progress", ""]
        self.done = ["TEAM-2", "Story", "Done child", "TEAM-1", "", "", "3", "Done", "1"]

    def write_csv(self, name, rows, headers=None, encoding="utf-8"):
        path = self.root / name
        with path.open("w", encoding=encoding, newline="") as output:
            csv.writer(output).writerows([headers or self.headers, *rows])
        return path

    def test_invalid_points_stop_before_completion_is_calculated(self):
        for value in ("five", "-2", "NaN", "Infinity", "1,5", "1e999"):
            with self.subTest(value=value):
                bad = ["TEAM-3", "Story", "Unfinished", "TEAM-1", "", "", value, "To Do", ""]
                path = self.write_csv("bad.csv", [self.epic, self.done, bad])
                with self.assertRaises(J2PError) as raised:
                    build_run_plan(path, self.config)
                for text in ("bad.csv", "row 4", "TEAM-3", "Story Points", value):
                    self.assertIn(text, str(raised.exception))

    def test_blank_zero_and_grouped_estimates_remain_distinct(self):
        self.assertIsNone(parse_number(""))
        self.assertEqual(parse_number("0"), 0)
        self.assertEqual(parse_number("1,000.25"), 1000.25)

    def test_duration_requires_complete_nonnegative_value(self):
        for value in ("1h 99unknown", "1h nonsense", "about a day", "-1", "-1h", "NaN", "1:99", "1,5"):
            with self.subTest(value=value), self.assertRaises(J2PError):
                parse_logged_hours(value, key="TEAM-3", row_index=4, source_file="bad.csv")
        self.assertEqual(parse_logged_hours(""), 0)
        self.assertEqual(parse_logged_hours("1h, 30m"), 1.5)
        self.assertAlmostEqual(parse_logged_hours("1:00:59"), 1 + 59 / 3600)
        self.assertAlmostEqual(parse_logged_hours("1s"), 1 / 3600)

    def test_numeric_hours_units_use_selected_alias_per_row(self):
        config = load_config(None, {
            "resource_groups": {"TEAM": "Team"}, "rollup_modes": {"TEAM": "fixVersion"},
            "metrics": {"logged_hours_units": {"Time Spent": "seconds"}},
        })
        headers = [*self.headers, "Time Spent"]
        path = self.write_csv("units.csv", [self.epic + [""], self.done + [""],
            ["TEAM-3", "Story", "Seconds", "TEAM-1", "", "", "3", "Done", "", "3600"]], headers)
        plan = build_run_plan(path, config)
        self.assertEqual(plan.epics["TEAM-1"].logged_hours, 2)
        self.assertEqual(parse_logged_hours("60", numeric_unit="minutes"), 1)

    def test_seconds_are_not_rounded_away_before_aggregation(self):
        rows = [self.epic]
        for number in range(2, 62):
            rows.append([f"TEAM-{number}", "Story", "One second", "TEAM-1", "", "", "1", "Done", "1s"])
        plan = build_run_plan(self.write_csv("seconds.csv", rows), self.config)
        self.assertEqual(plan.epics["TEAM-1"].logged_hours, 0.02)

    def test_orphans_wrong_parent_and_excluded_epics_are_reconciled(self):
        excluded = ["OTHER-1", "Epic", "Excluded", "", "", "Release 1", "", "To Do", ""]
        rows = [self.epic, self.done, excluded,
                ["TEAM-3", "Story", "Missing", "TEAM-99", "", "", "5", "Done", "1"],
                ["TEAM-4", "Story", "Wrong type", "TEAM-2", "", "", "5", "Done", "1"],
                ["TEAM-5", "Story", "Excluded parent", "OTHER-1", "", "", "5", "Done", "1"],
                ["TEAM-6", "Story", "Unlinked", "", "", "", "5", "Done", "1"]]
        plan = build_run_plan(self.write_csv("parents.csv", rows), self.config)
        categories = {item.category: item for item in plan.audit_items}
        for name in ("StoryEpicNotFound", "StoryParentNotEpic", "StoryEpicExcluded", "StoryMissingEpicLink"):
            self.assertIn(name, categories)
            self.assertIn("parents.csv", categories[name].source_file)
        self.assertEqual(plan.stats["story_rows_read"], 5)
        self.assertEqual(plan.stats["story_rows_attached_to_epic"], 2)
        self.assertEqual(plan.stats["story_rows_used_for_completion"], 1)
        self.assertEqual(plan.stats["story_rows_omitted_from_completion"], 4)
        self.assertEqual(plan.epics["TEAM-1"].total_story_points, 3)

    def test_parent_reconciliation_happens_after_all_batches(self):
        first = self.write_csv("child.csv", [self.done])
        second = self.write_csv("parent.csv", [self.epic], encoding="utf-16")
        plan = build_run_plan([first, second, first], self.config)
        self.assertFalse(any(item.category == "StoryEpicNotFound" for item in plan.audit_items))
        self.assertEqual(plan.stats["unique_issues_read"], 2)
        batches = plan.stats["csv_batches"]
        self.assertEqual([batch["unique_issues_added"] for batch in batches], [1, 1, 0])
        self.assertEqual([batch["duplicates_skipped"] for batch in batches], [0, 0, 1])
        self.assertEqual(batches[1]["encoding"], "utf-16")
        self.assertEqual(batches[1]["path"], str(second.resolve()))
        self.assertEqual(batches[0]["column_map"]["story_points"], "Story Points")

    def test_duplicate_conflicts_name_changed_fields(self):
        first = self.write_csv("first.csv", [self.epic, self.done])
        changed = list(self.done)
        changed[6], changed[7] = "4", "To Do"
        second = self.write_csv("second.csv", [changed])
        with self.assertRaisesRegex(J2PError, "Conflicting fields: status, story_points"):
            build_run_plan([first, second], self.config)

    def test_large_unused_field_supported_but_configured_cap_is_enforced(self):
        headers = [*self.headers, "Description"]
        path = self.write_csv("large.csv", [self.epic + ["x" * 150000]], headers)
        plan = build_run_plan(path, self.config)
        self.assertIn("TEAM-1", plan.epics)
        previous_limit = csv.field_size_limit()
        with self.assertRaisesRegex(J2PError, "large.csv.*physical line.*field larger"):
            CsvTable(path, max_field_chars=140000)
        self.assertEqual(csv.field_size_limit(), previous_limit)

    def test_malformed_csv_reports_file_and_location(self):
        path = self.root / "quotes.csv"
        path.write_text('Issue key,Summary\nTEAM-1,"unfinished\n')
        with self.assertRaisesRegex(J2PError, "quotes.csv.*physical line 2"):
            CsvTable(path)
        path = self.write_csv("width.csv", [self.epic[:-1]])
        with self.assertRaisesRegex(J2PError, "width.csv, row 2: expected 9 columns, got 8"):
            CsvTable(path)

    def test_aggregate_time_requires_explicit_nonoverlapping_mode(self):
        headers = [*self.headers[:-1], "Σ Time Spent"]
        path = self.write_csv("aggregate.csv", [self.epic, self.done], headers)
        with self.assertRaisesRegex(J2PError, "requires metrics.logged_hours_source"):
            build_run_plan(path, self.config)
        config = load_config(None, {
            "resource_groups": {"TEAM": "Team"}, "rollup_modes": {"TEAM": "fixVersion"},
            "metrics": {"logged_hours_source": "aggregate"},
        })
        self.assertEqual(build_run_plan(path, config).epics["TEAM-1"].logged_hours, 1)
        child = ["TEAM-3", "Sub-task", "Nested", "TEAM-1", "TEAM-2", "", "1", "Done", "0.5"]
        path = self.write_csv("overlap.csv", [self.epic, self.done, child], headers)
        with self.assertRaisesRegex(J2PError, "aggregate logged time overlaps exported parent TEAM-2"):
            build_run_plan(path, config)


class ConfigReliabilityTests(unittest.TestCase):
    def test_config_types_unknown_keys_and_ranges_are_checked(self):
        cases = [
            {"columns": None}, {"behavior": []}, {"metrcs": {}},
            {"metrics": {"hours_per_story_point": True}},
            {"metrics": {"logged_hours_unit": "days"}},
            {"metrics": {"logged_hours_units": {"Time Spent": "days"}}},
            {"planning_horizon": {"enabled": "false"}},
            {"planning_horizon": {"bucket_months": 1.5}},
            {"planning_horizon": {"bucket_months": True}},
            {"planning_horizon": {"as_of_date": "not-a-date"}},
            {"behavior": {"hidden_completed_epics": True}},
            {"behavior": {"unknown_prefix": "include"}},
            {"columns": {"summary": [None]}},
            {"issue_types": {"epic": []}},
            {"done_statuses": [True]},
            {"input": {"csv_max_field_chars": 65 * 1024 * 1024}},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ConfigError):
                load_config(None, overrides)

    def test_quoted_hash_colon_commas_and_escapes_are_preserved(self):
        parsed = parse_yaml_subset('''resource_groups:
  TEAM: "Team #1: Alpha" # comment
done_statuses: ["Done, final", 'It''s closed']
colors:
  changed_cell: '#C6EFCE'
''')
        self.assertEqual(parsed["resource_groups"]["TEAM"], "Team #1: Alpha")
        self.assertEqual(parsed["done_statuses"], ["Done, final", "It's closed"])
        self.assertEqual(parsed["colors"]["changed_cell"], "#C6EFCE")

    def test_yaml_unsupported_or_duplicate_constructs_fail_with_line(self):
        for text in ('x: 1\nx: 2', 'x: &defaults hi', 'x: *defaults', 'x: |\n  line',
                     'x: {a: b}', 'x: ["unterminated]', 'x: [a, [b]]',
                     'x: 1\n  bad: 2', '---\nx: 1'):
            with self.subTest(text=text), self.assertRaisesRegex(ConfigError, "[Ll]ine"):
                parse_yaml_subset(text)

    def test_installed_yaml_module_cannot_change_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text('resource_groups:\n  TEAM: "Team #1"\nrollup_modes:\n  TEAM: fixVersion\n')
            original = load_config(path)
            class ForbiddenYaml:
                def safe_load(self, text):
                    raise AssertionError("Optional parser must never be imported")
            with patch.dict(sys.modules, {"yaml": ForbiddenYaml()}):
                self.assertEqual(load_config(path), original)

    def test_duplicate_prefix_and_units_after_normalization_fail(self):
        for overrides in (
            {"resource_groups": {"TEAM": "One", "team": "Two"}},
            {"metrics": {"logged_hours_units": {"Time Spent": "hours", "Time   Spent": "seconds"}}},
        ):
            with self.assertRaisesRegex(ConfigError, "duplicate"):
                load_config(None, overrides)


if __name__ == "__main__":
    unittest.main()
