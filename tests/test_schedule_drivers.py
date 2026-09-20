"""Observed schedule drivers stay focused without losing their raw evidence."""

import copy
import unittest
from dataclasses import asdict

from j2p.cascade import CascadeGraph
from j2p.models import AuditItem, PlanEpic, RunPlan
from j2p.schedule_drivers import build_schedule_drivers


def driver_plan(links, fields=None):
    epics = {}
    audit = []
    for key, following in links.items():
        epics[key] = PlanEpic(
            key=key, jira_key=key, issue_id=key, summary=key,
            status="Open", rollup_mode="initiative", rollup_key="INIT-1",
            rollup_name="Initiative", resource_group="Alpha", key_prefix="TEAM",
            total_story_points=1, completed_story_points=0, logged_hours=0,
            completed_logged_hours=0, story_point_ratio=0, percent_complete=0,
            in_planning=False, completed=False, target_start="2026-09-01",
            target_end="2026-09-04", successors=list(following),
        )
        for field in (fields or {}).get(key, ["Finish"]):
            audit.append(AuditItem(
                "Info", "ScheduledStartChange" if field == "Start" else "CascadingDateChange",
                jira_key=key, schedule_key=key, field=field,
                old_value="2026-09-01", new_value="2026-09-02",
            ))
    return RunPlan("2026-09-01", "fixture.csv", "initiative", {}, {}, {}, epics, audit)


def undate(plan, key):
    plan.epics[key].target_start = ""
    plan.epics[key].target_end = ""


class ScheduleDriverTests(unittest.TestCase):
    def test_upstream_finish_with_start_only_successor_has_schedule_impact(self):
        plan = driver_plan({"A": ["B"], "B": []}, {"B": ["Start"]})
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["driver_keys"], {"A"})
        self.assertEqual(result["graph"].downstream_counts, {"B": 0, "A": 1})
        self.assertEqual(set(result["changes"]["B"]), {"Start"})

    def test_start_only_upstream_does_not_drive_finish_to_start_links(self):
        plan = driver_plan({"A": ["B"], "B": []}, {"A": ["Start"]})
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], [])
        self.assertEqual(result["graph"].successors, {"A": [], "B": []})

    def test_isolated_start_and_finish_changes_are_not_drivers(self):
        plan = driver_plan({"A": [], "B": []}, {"A": ["Start"]})
        result = build_schedule_drivers(plan)
        self.assertEqual(set(result["changes"]), {"A", "B"})
        self.assertEqual(result["roots"], [])

    def test_mismatch_only_and_non_native_changes_never_establish_impact(self):
        plan = driver_plan({"A": ["B"], "B": []})
        plan.audit_items[0].category = "ScheduledDateMismatch"
        plan.audit_items[1].field = "Jira Target End"
        result = build_schedule_drivers(plan)
        self.assertEqual(result["changes"], {})
        self.assertEqual(result["roots"], [])

    def test_equal_or_blank_dates_are_not_recorded_shifts(self):
        plan = driver_plan({"A": ["B"], "B": ["C"], "C": []})
        plan.audit_items[0].old_value = plan.audit_items[0].new_value
        plan.audit_items[1].old_value = ""
        plan.audit_items[2].new_value = ""
        self.assertEqual(build_schedule_drivers(plan)["changes"], {})

    def test_legacy_blank_field_infers_finish_and_keeps_audit_unchanged(self):
        plan = driver_plan({"A": ["B"], "B": []})
        plan.audit_items[0].category = "CascadeBranchDriver"
        plan.audit_items[0].field = ""
        before = copy.deepcopy(asdict(plan))
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertIs(result["changes"]["A"]["Finish"], plan.audit_items[0])
        self.assertEqual(asdict(plan), before)

    def test_undated_ancestor_does_not_hide_nested_dated_driver(self):
        plan = driver_plan({"A": ["B"], "B": ["C"], "C": []})
        undate(plan, "A")
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["B"])
        self.assertEqual(result["driver_keys"], {"B"})

    def test_undated_and_completed_context_do_not_inflate_impact_counts(self):
        plan = driver_plan({"A": ["B", "C"], "B": ["D"], "C": [], "D": []})
        undate(plan, "B")
        plan.epics["C"].completed = True
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["graph"].downstream_counts["A"], 1)
        self.assertEqual(result["graph"].reachable(["A"]), {"A", "B", "D"})
        self.assertEqual(result["driver_keys"], {"A"})
        self.assertIn("C", result["changes"])

    def test_context_only_dead_end_chain_is_pruned_but_full_audit_remains(self):
        plan = driver_plan({
            "A": ["B", "C"], "B": [], "C": ["D"], "D": ["E"], "E": [],
        })
        plan.epics["C"].completed = True
        undate(plan, "D")
        plan.epics["E"].completed = True
        before = copy.deepcopy(asdict(plan))
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["graph"].successors, {"A": ["B"], "B": []})
        self.assertEqual(result["graph"].downstream_counts["A"], 1)
        self.assertEqual(set(result["changes"]), set(plan.epics))
        self.assertEqual(asdict(plan), before)

    def test_completed_and_undated_context_chain_to_dated_work_is_retained(self):
        plan = driver_plan({"A": ["B"], "B": ["C"], "C": ["D"], "D": []})
        plan.epics["B"].completed = True
        undate(plan, "C")
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["graph"].reachable(["A"]), {"A", "B", "C", "D"})
        self.assertEqual(result["graph"].downstream_counts["A"], 1)

    def test_only_undated_affected_work_does_not_qualify_a_driver(self):
        plan = driver_plan({"A": ["B"], "B": []})
        undate(plan, "B")
        self.assertEqual(build_schedule_drivers(plan)["roots"], [])

    def test_completed_upstream_can_explain_unfinished_work_but_complete_only_is_hidden(self):
        plan = driver_plan({"A": ["B"], "B": []})
        plan.epics["A"].completed = True
        self.assertEqual(build_schedule_drivers(plan)["roots"], ["A"])
        plan.epics["B"].completed = True
        self.assertEqual(build_schedule_drivers(plan)["roots"], [])

    def test_partial_jira_dates_are_sufficient_for_dated_work(self):
        plan = driver_plan({"A": ["B"], "B": []})
        plan.epics["A"].target_end = ""
        plan.epics["B"].target_start = ""
        self.assertEqual(build_schedule_drivers(plan)["roots"], ["A"])

    def test_resource_group_filter_selects_nested_driver_and_keeps_cross_team_descendants(self):
        plan = driver_plan({"A": ["B"], "B": ["C"], "C": ["D"], "D": []})
        plan.epics["A"].resource_group = "Other"
        plan.epics["C"].resource_group = "Other"
        plan.epics["D"].resource_group = "Other"
        self.assertEqual(build_schedule_drivers(plan)["roots"], ["A"])
        result = build_schedule_drivers(plan, root_resource_group="Alpha")
        self.assertEqual(result["roots"], ["B"])
        self.assertEqual(result["graph"].reachable(result["roots"]), {"B", "C", "D"})
        self.assertEqual(result["driver_keys"], {"A", "B", "C"})
        self.assertEqual(build_schedule_drivers(plan, "Missing")["roots"], [])

    def test_shared_descendant_counts_once_and_roots_sort_by_impact_then_key(self):
        plan = driver_plan({
            "A": ["B", "C"], "B": ["D"], "C": ["D"], "D": [],
            "Z": ["Y"], "Y": [], "E": ["F"], "F": [],
        })
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A", "E", "Z"])
        self.assertEqual(result["graph"].downstream_counts["A"], 3)

    def test_reference_rows_and_missing_targets_cannot_create_branches(self):
        plan = driver_plan({"A": ["B", "UNKNOWN"], "B": []})
        plan.epics["B"].drives_schedule = False
        result = build_schedule_drivers(plan)
        self.assertEqual(set(result["changes"]), {"A"})
        self.assertEqual(result["roots"], [])

    def test_accepted_relationship_can_be_represented_by_predecessors(self):
        plan = driver_plan({"A": [], "B": []})
        plan.epics["B"].predecessors = ["a", "UNKNOWN", "B"]
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["graph"].successors, {"A": ["B"], "B": []})

    def test_start_only_intermediate_does_not_claim_impact_on_later_finish(self):
        plan = driver_plan({"A": ["B"], "B": ["C"], "C": []}, {"B": ["Start"]})
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["A"])
        self.assertEqual(result["graph"].reachable(["A"]), {"A", "B"})
        self.assertEqual(result["graph"].downstream_counts["A"], 1)

    def test_long_chain_is_iterative_and_uses_only_topmost_driver(self):
        count = 1500
        plan = driver_plan({f"K{n:04}": [f"K{n+1:04}"] if n + 1 < count else [] for n in range(count)})
        result = build_schedule_drivers(plan)
        self.assertEqual(result["roots"], ["K0000"])
        self.assertEqual(result["graph"].downstream_counts["K0000"], count - 1)

    def test_weighted_graph_preserves_default_counts_and_cycle_safety(self):
        links = {"A": ["B"], "B": ["A", "C"], "C": []}
        self.assertEqual(CascadeGraph(links).downstream_counts, {"A": 2, "B": 2, "C": 0})
        self.assertEqual(CascadeGraph(links, counted_keys={"C"}).downstream_counts, {"A": 1, "B": 1, "C": 0})
        self.assertEqual(CascadeGraph(links, counted_keys=set()).downstream_counts, {"A": 0, "B": 0, "C": 0})
        self.assertEqual(build_schedule_drivers(driver_plan(links))["roots"], [])


if __name__ == "__main__":
    unittest.main()
