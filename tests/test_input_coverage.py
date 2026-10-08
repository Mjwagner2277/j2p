"""Source project coverage is independent of planned rows and file names."""

import copy
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace

from j2p.input_coverage import coverage_warnings, project_coverage


def issue(key, issue_type="Epic", **values):
    return SimpleNamespace(key=key, issue_type=issue_type, **values)


class InputCoverageTests(unittest.TestCase):
    def test_all_issue_types_and_projects_in_mixed_exports_establish_coverage(self):
        issues = [issue("BBB-1", "Task"), issue("AAA-1", "Epic"),
                  issue("CCC-1", "Initiative"), issue("BBB-2", "Story")]
        config = {"resource_groups": {"CCC": "Third", "AAA": "First", "BBB": "Second", "DDD": "Missing"}}
        self.assertEqual(project_coverage(issues, config), {
            "configured_projects": ["AAA", "BBB", "CCC", "DDD"],
            "input_projects": ["AAA", "BBB", "CCC"],
            "missing_projects": ["DDD"],
            "unconfigured_projects": [],
            "issue_counts_by_project": {"AAA": 1, "BBB": 2, "CCC": 1},
        })

    def test_unaccepted_versions_still_establish_project_coverage(self):
        issues = [issue("AAA-1", fix_versions=["Private"]), issue("BBB-1", fix_versions=[])]
        config = {
            "resource_groups": {"AAA": "First", "BBB": "Second"},
            "fixversion_scope": {"enabled": True, "accepted": ["Shared"]},
        }
        coverage = project_coverage(issues, config)
        self.assertEqual(coverage["input_projects"], ["AAA", "BBB"])
        self.assertEqual(coverage_warnings(coverage, "exports"), [])

    def test_normalization_duplicate_keys_and_deterministic_order(self):
        issues = [issue(" zzz-2 "), issue("aaa-1"), issue("ZZZ-2"), issue("zzz-1"), issue("")]
        config = {"resource_groups": {" bbb ": "Missing", "aaa": "First"}}
        before = copy.deepcopy((issues, config))
        expected = {
            "configured_projects": ["AAA", "BBB"], "input_projects": ["AAA", "ZZZ"],
            "missing_projects": ["BBB"], "unconfigured_projects": ["ZZZ"],
            "issue_counts_by_project": {"AAA": 1, "ZZZ": 2},
        }
        self.assertEqual(project_coverage(iter(issues), config), expected)
        self.assertEqual(project_coverage(reversed(issues), config), expected)
        self.assertEqual(list(project_coverage(issues, config)["issue_counts_by_project"]), ["AAA", "ZZZ"])
        self.assertEqual((issues, config), before)

    def test_empty_input_and_empty_configuration(self):
        self.assertEqual(project_coverage([], {"resource_groups": {"BBB": "Missing"}}), {
            "configured_projects": ["BBB"], "input_projects": [], "missing_projects": ["BBB"],
            "unconfigured_projects": [], "issue_counts_by_project": {},
        })
        self.assertEqual(project_coverage([issue("AAA-1")], {})["unconfigured_projects"], ["AAA"])

    def test_warnings_explain_partial_scope_without_fabricated_jira_keys(self):
        coverage = project_coverage(
            [issue("ZZZ-1")], {"resource_groups": {"CCC": "Missing", "BBB": "Missing"}},
        )
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            warnings = coverage_warnings(coverage, "folder with spaces")
        self.assertEqual((output.getvalue(), error.getvalue()), ("", ""))
        self.assertEqual([(item.category, item.old_value) for item in warnings], [
            ("MissingProjectExport", "BBB"), ("MissingProjectExport", "CCC"),
            ("UnconfiguredJiraProject", "ZZZ"),
        ])
        for warning in warnings:
            self.assertEqual(warning.severity, "Warning")
            self.assertEqual(warning.jira_key, "")
            self.assertEqual(warning.schedule_key, "")
            self.assertEqual(warning.source_file, "folder with spaces")
            self.assertIn("folder with spaces", warning.message)
            self.assertIn(warning.old_value, warning.reviewer_action)
        self.assertIn("Totals and cross-project links may be incomplete", warnings[0].message)
        self.assertIn("partial scope is intentional", warnings[0].reviewer_action)
        self.assertIn("epics and initiative estimates are excluded", warnings[-1].message)
        self.assertIn("linked child issues can still contribute", warnings[-1].message)

    def test_warning_groups_can_be_disabled_independently(self):
        coverage = project_coverage([issue("ZZZ-1")], {"resource_groups": {"AAA": "Missing"}})
        self.assertEqual([item.category for item in coverage_warnings(coverage, "", warn_missing=False)],
                         ["UnconfiguredJiraProject"])
        self.assertEqual([item.category for item in coverage_warnings(coverage, "", warn_unconfigured=False)],
                         ["MissingProjectExport"])
        self.assertEqual(coverage_warnings(coverage, "", warn_missing=False, warn_unconfigured=False), [])
        self.assertIn("selected CSV exports", coverage_warnings(coverage, "")[0].message)


if __name__ == "__main__":
    unittest.main()
