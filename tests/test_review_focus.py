"""Review priorities preserve evidence while separating completed legacy work."""

import unittest
from copy import deepcopy

from j2p.models import AuditItem, PlanEpic, RunPlan
from j2p.review_focus import build_review_focus


def context(completed=False, start="", end="", parent="", **extra):
    return {
        "summary": "Issue summary", "completed": completed,
        "target_start": start, "target_end": end,
        "parent_epic": parent, "resource_group": "Software", **extra,
    }


def audit(key, category="InPlanning", severity="Review", parent="", **extra):
    return AuditItem(severity, category, jira_key=key, old_value=parent, **extra)


def epic(key, completed=False, jira_key="", **extra):
    return PlanEpic(
        key=key, jira_key=jira_key or key, issue_id="", summary=key,
        status="Done" if completed else "Open", completed=completed,
        rollup_mode="fixVersion", rollup_key="Release", rollup_name="Release",
        resource_group="Software", key_prefix="SW", total_story_points=3,
        completed_story_points=3 if completed else 0, logged_hours=0,
        completed_logged_hours=0, story_point_ratio=0,
        percent_complete=100 if completed else 0, in_planning=False,
        target_start=extra.pop("target_start", ""), target_end=extra.pop("target_end", ""), **extra,
    )


def plan(items, contexts=None, epics=None):
    return RunPlan(
        generated_at="2026-09-19T12:00:00", jira_csv="unchanged.csv", rollup_mode="mixed",
        column_map={}, stats={"review_issue_context": contexts or {}},
        summaries={}, epics=epics or {}, audit_items=items,
    )


class ReviewFocusTests(unittest.TestCase):
    def test_completed_legacy_children_are_one_historical_parent_action(self):
        items = [audit(f"SW-{number}", "StoryEpicNotFound", "Warning", parent="SW-99") for number in range(1, 4)]
        source = plan(items, {f"SW-{number}": context(True, end="2022-01-01", parent="SW-99") for number in range(1, 4)})
        before = deepcopy(source)
        result = build_review_focus(source)
        self.assertEqual((result["focus_count"], result["historical_count"], result["grouped_count"]), (0, 1, 1))
        group = result["groups"][0]
        self.assertEqual((group["key"], group["audit_count"], group["issue_count"]), ("SW-99", 3, 3))
        self.assertEqual(group["items"], items)
        self.assertEqual(source, before)

    def test_past_dates_stay_current_and_unknown_dates_stay_unscheduled(self):
        source = plan([audit("SW-1"), audit("SW-2")], {"SW-1": context(end="2020-01-01")})
        groups = {group["key"]: group for group in build_review_focus(source)["groups"]}
        self.assertEqual(groups["SW-1"]["tier"], "Focus now")
        self.assertTrue(any("past target end" in reason for reason in groups["SW-1"]["reasons"]))
        self.assertEqual(groups["SW-2"]["tier"], "Unscheduled")
        self.assertTrue(any("No Jira target dates" in reason for reason in groups["SW-2"]["reasons"]))

    def test_window_uses_own_dates_and_zero_days_includes_all_unfinished(self):
        source = plan([
            audit("SW-1", "FutureInPlanning", "Info", planning_date="2010-01-01"),
            audit("SW-2", "InPlanning", planning_date="2030-01-01"),
            audit("SW-3"), audit("SW-4"),
        ], {
            "SW-1": context(start="2028-01-01", end="2028-02-01"),
            "SW-2": context(start="2026-09-01", end="2028-02-01"),
            "SW-3": context(end="2026-12-18"),
            "SW-4": context(end="2026-12-19"),
        })
        groups = {group["key"]: group for group in build_review_focus(source)["groups"]}
        self.assertEqual([groups[f"SW-{n}"]["tier"] for n in range(1, 5)], ["Later", "Focus now", "Focus now", "Later"])
        self.assertEqual(build_review_focus(source, days=0)["focus_count"], 4)

    def test_errors_and_unresolved_dependencies_never_hidden(self):
        source = plan([
            audit("SW-1", "UnexpectedFailure", "Error"),
            audit("SW-2", "MissingDependencyTarget", "Warning"),
            audit("SW-3", "ProjectDependencyWriteFailed", "Warning"),
            audit("SW-4", "CircularDependencySkipped", "Warning"),
            audit("SW-5", "ProjectDateWriteFailed", "Warning"),
        ], {
            "SW-1": context(True, end="2020-01-01"),
            "SW-2": context(True, end="2020-01-01"),
            "SW-3": context(start="2030-01-01"),
            "SW-4": context(start="2030-01-01"),
            "SW-5": context(True, end="2020-01-01"),
        })
        self.assertTrue(all(group["tier"] == "Fix first" for group in build_review_focus(source)["groups"]))

    def test_schedule_reference_warnings_group_by_jira_identity(self):
        source = plan([
            audit("SW-1", "ProjectNativeCompletionRecalculated", "Warning", schedule_key="SW-1::A"),
            audit("SW-1", "ProjectNativeCompletionRecalculated", "Warning", schedule_key="SW-1::B"),
        ], epics={
            "SW-1::A": epic("SW-1::A", jira_key="SW-1"),
            "SW-1::B": epic("SW-1::B", jira_key="SW-1", drives_schedule=False),
        })
        group = build_review_focus(source)["groups"][0]
        self.assertEqual((group["tier"], group["audit_count"], group["issue_count"]), ("Unscheduled", 2, 1))
        self.assertEqual(group["categories"], ["ProjectNativeCompletionRecalculated"])

    def test_completed_upstream_with_unfinished_descendant_remains_focus(self):
        source = plan([audit("SW-1")], epics={
            "SW-1::A": epic("SW-1::A", True, jira_key="SW-1", target_end="2026-09-20", successors=["SW-2::A"]),
            "SW-1::B": epic("SW-1::B", True, jira_key="SW-1", drives_schedule=False, successors=["SW-2::A"]),
            "SW-2::A": epic("SW-2::A", True, jira_key="SW-2", successors=["SW-3"]),
            "SW-3": epic("SW-3"),
        })
        group = build_review_focus(source)["groups"][0]
        self.assertEqual((group["tier"], group["downstream_count"]), ("Focus now", 1))

    def test_filtered_view_uses_full_dependency_scope_without_other_team_audits(self):
        all_epics = {
            "SW-1": epic("SW-1", True, target_end="2026-09-20", successors=["HW-1"]),
            "HW-1": epic("HW-1"),
        }
        whole = plan([audit("SW-1"), audit("HW-1")], epics=all_epics)
        filtered = plan([whole.audit_items[0]], epics={"SW-1": all_epics["SW-1"]})
        result = build_review_focus(filtered, dependency_plan=whole)
        self.assertEqual((result["grouped_count"], result["focus_count"]), (1, 1))
        self.assertEqual((result["groups"][0]["key"], result["groups"][0]["downstream_count"]), ("SW-1", 1))
        whole.epics["HW-1"].completed = True
        self.assertEqual(build_review_focus(filtered, dependency_plan=whole)["historical_count"], 1)

    def test_open_parent_prevents_completed_omission_children_becoming_historical(self):
        source = plan([
            audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99"),
            audit("SW-99", "ExcludedMissingRollup", "Warning"),
        ], {
            "SW-1": context(True, end="2020-01-01", parent="SW-99"),
            "SW-99": context(False, end="2026-09-20"),
        })
        group = build_review_focus(source)["groups"][0]
        self.assertEqual((group["key"], group["tier"], group["audit_count"]), ("SW-99", "Fix first", 2))

    def test_open_sibling_prevents_completed_parent_and_audited_child_becoming_historical(self):
        source = plan([audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99")], {
            "SW-1": context(True, parent="SW-99"),
            "SW-2": context(False, end="2026-09-20", parent="SW-99"),
            "SW-99": context(True),
        })
        group = build_review_focus(source)["groups"][0]
        self.assertEqual((group["tier"], group["issue_count"]), ("Fix first", 1))

    def test_missing_initiative_groups_affected_epics_and_children_once(self):
        source = plan([
            audit("SW-10", "ExcludedMissingRollup", "Warning"),
            audit("SW-20", "ExcludedMissingRollup", "Warning"),
            audit("SW-11", "StoryEpicExcluded", "Warning", parent="SW-10"),
            audit("SW-21", "StoryEpicExcluded", "Warning", parent="SW-20"),
        ], {
            "SW-10": context(end="2026-10-01", missing_rollup_parent="INIT-1"),
            "SW-20": context(end="2026-11-01", missing_rollup_parent="INIT-1"),
            "SW-11": context(True, end="2020-01-01", parent="SW-10"),
            "SW-21": context(True, parent="SW-20"),
        })
        result = build_review_focus(source)
        self.assertEqual(result["grouped_count"], 1)
        group = result["groups"][0]
        self.assertEqual((group["key"], group["summary"], group["tier"]), ("INIT-1", "Missing initiative INIT-1", "Fix first"))
        self.assertEqual((group["target_end"], group["issue_count"], group["audit_count"]), ("2026-10-01", 4, 4))
        self.assertIn("Review this missing initiative once for 2 excluded epic(s) and 2 omitted child issue(s).", group["reasons"])
        self.assertTrue(group["actions"][0].startswith("Include initiative INIT-1 in the Jira export"))

    def test_future_excluded_scope_can_wait_despite_old_completed_child(self):
        source = plan([
            audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99"),
            audit("SW-99", "ExcludedMissingRollup", "Warning"),
        ], {
            "SW-1": context(True, end="2020-01-01", parent="SW-99"),
            "SW-99": context(False, start="2028-01-01", end="2028-02-01"),
        })
        self.assertEqual(build_review_focus(source)["groups"][0]["tier"], "Later")

    def test_invalid_dates_and_project_failures_take_priority(self):
        source = plan([
            audit("SW-1"), audit("SW-2", "ProjectDateWriteFailed", "Warning"),
            audit("SW-3", "UnparsedDate", "Warning"),
            audit("SW-4", "ProjectNativeCompletionRecalculated", "Warning"),
        ], {
            "SW-1": context(start="2028-01-02", end="2028-01-01"),
            "SW-2": context(start="2028-01-01"),
            "SW-3": context(end="bad date"),
            "SW-4": context(start="2028-01-01"),
        })
        groups = build_review_focus(source)["groups"]
        self.assertEqual(groups[0]["key"], "SW-2")
        self.assertEqual([group["tier"] for group in groups], ["Fix first", "Fix first", "Fix first", "Later"])

    def test_impact_ranking_and_key_ties_are_deterministic(self):
        items = [
            audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99"),
            audit("SW-2", "StoryEpicExcluded", "Warning", parent="SW-99"),
            audit("SW-3", "MissingDependencyTarget", "Warning"),
            audit("SW-4", "ProjectDateWriteFailed", "Warning"),
            audit("SW-5", "UnexpectedFailure", "Error"),
        ]
        source = plan(items, {
            "SW-1": context(end="2026-09-20", parent="SW-99"),
            "SW-2": context(end="2026-09-20", parent="SW-99"),
            **{f"SW-{n}": context(end="2026-09-20") for n in (3, 4, 5)},
        })
        result = build_review_focus(source)
        self.assertEqual([group["key"] for group in result["groups"]], ["SW-4", "SW-5", "SW-99", "SW-3"])
        source.audit_items.reverse()
        self.assertEqual(build_review_focus(source), result)

    def test_nonreview_info_is_not_promoted_and_rows_without_keys_stay_distinct(self):
        source = plan([
            audit("SW-1", "AddedEpic", "Info"),
            audit("", "CsvRowMissingJiraKey", "Warning", source_file="a.csv", source_row=2),
            audit("", "CsvRowMissingJiraKey", "Warning", source_file="a.csv", source_row=3),
        ])
        result = build_review_focus(source)
        self.assertEqual((result["total_audit_count"], result["grouped_count"]), (2, 2))
        self.assertTrue(all(group["tier"] == "Focus now" for group in result["groups"]))


    def test_undated_task_failures_and_cascades_stay_outside_high_priority(self):
        for category, severity in (
            ("InPlanning", "Review"),
            ("MissingDependencyTarget", "Warning"),
            ("ProjectDateWriteFailed", "Warning"),
            ("CascadeBranchDriver", "Review"),
            ("UnexpectedFailure", "Error"),
        ):
            with self.subTest(category=category):
                source = plan([audit("SW-1", category, severity,
                                     planning_date="2026-09-19", planning_bucket="Immediate",
                                     field="Finish", new_value="2026-09-19")], epics={
                    "SW-1": epic("SW-1", successors=["SW-2"]),
                    "SW-2": epic("SW-2", target_end="2026-09-20"),
                })
                original = deepcopy(source)
                for days in (90, 0):
                    result = build_review_focus(source, days=days)
                    group = result["groups"][0]
                    self.assertEqual((result["focus_count"], result["unscheduled_count"]), (0, 1))
                    self.assertEqual((group["tier"], group["downstream_count"]), ("Unscheduled", 1))
                    self.assertIs(group["items"][0], source.audit_items[0])
                self.assertEqual(source, original)

    def test_partial_jira_dates_still_establish_priority(self):
        source = plan([audit("SW-1"), audit("SW-2")], {
            "SW-1": context(start="2026-09-20"),
            "SW-2": context(end="2026-09-20"),
        })
        result = build_review_focus(source)
        self.assertEqual((result["focus_count"], result["unscheduled_count"]), (2, 0))

    def test_undated_scope_group_remains_unscheduled_with_completed_dated_sibling(self):
        source = plan([
            audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99"),
            audit("SW-99", "ExcludedMissingRollup", "Warning"),
        ], {
            "SW-1": context(True, end="2020-01-01", parent="SW-99"),
            "SW-2": context(False, parent="SW-99"),
            "SW-99": context(False),
        })
        result = build_review_focus(source)
        self.assertEqual((result["focus_count"], result["unscheduled_count"]), (0, 1))
        self.assertEqual(result["groups"][0]["audit_count"], 2)

    def test_undated_sibling_does_not_promote_future_group_into_focus(self):
        source = plan([audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99")], {
            "SW-1": context(parent="SW-99"),
            "SW-2": context(start="2028-01-01", parent="SW-99"),
            "SW-99": context(),
        })
        result = build_review_focus(source)
        self.assertEqual((result["later_count"], result["focus_count"]), (1, 0))
        self.assertEqual(build_review_focus(source, days=0)["focus_count"], 1)
        # An unavailable child context also must not make its future group urgent.
        del source.stats["review_issue_context"]["SW-1"]
        self.assertEqual(build_review_focus(source)["groups"][0]["tier"], "Later")

    def test_current_child_date_anchors_undated_parent_group(self):
        source = plan([audit("SW-1", "StoryEpicExcluded", "Warning", parent="SW-99")], {
            "SW-1": context(end="2026-09-20", parent="SW-99"),
            "SW-99": context(),
        })
        result = build_review_focus(source)
        self.assertEqual((result["focus_count"], result["unscheduled_count"]), (1, 0))
        self.assertEqual(result["groups"][0]["tier"], "Fix first")

    def test_completed_undated_work_remains_historical(self):
        source = plan([audit("SW-1")], {"SW-1": context(True)})
        result = build_review_focus(source)
        self.assertEqual((result["historical_count"], result["unscheduled_count"]), (1, 0))

    def test_invalid_supplied_date_stays_an_error_after_parser_normalizes_it_to_blank(self):
        source = plan([AuditItem("Warning", "UnparsedDate", jira_key="SW-1", old_value="not a date")],
                      {"SW-1": context()})
        result = build_review_focus(source)
        self.assertEqual(result["groups"][0]["tier"], "Fix first")
        self.assertEqual(result["unscheduled_count"], 0)

    def test_run_errors_without_a_task_identity_stay_high_priority(self):
        source = plan([audit("", "UnexpectedFailure", "Error")])
        self.assertEqual(build_review_focus(source)["groups"][0]["tier"], "Fix first")

    def test_cross_team_dependency_impact_does_not_promote_undated_task(self):
        all_epics = {
            "SW-1": epic("SW-1", successors=["HW-1"]),
            "HW-1": epic("HW-1", target_end="2026-09-20"),
        }
        whole = plan([audit("SW-1", "CascadeBranchDriver"), audit("HW-1")], epics=all_epics)
        filtered = plan([whole.audit_items[0]], epics={"SW-1": all_epics["SW-1"]})
        result = build_review_focus(filtered, dependency_plan=whole)
        self.assertEqual((result["focus_count"], result["unscheduled_count"]), (0, 1))
        self.assertEqual(result["groups"][0]["downstream_count"], 1)


if __name__ == "__main__":
    unittest.main()
