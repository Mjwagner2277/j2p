"""Cascade output stays bounded while complete audit data remains available."""

import csv
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from j2p.cascade import CASCADE_MAX_DEPTH, CASCADE_MAX_ENTRIES, CascadeGraph
from j2p.config import DEFAULT_CONFIG
from j2p.models import AuditItem, PlanEpic, RunPlan
from j2p.reports import render_schedule_cascade_review, write_reports


def graph_plan(successors):
    epics = {}
    audit = []
    for key, following in successors.items():
        epics[key] = PlanEpic(
            key=key, jira_key=key, issue_id=key, summary=key + " summary",
            status="Open", rollup_mode="fixVersion", rollup_key="Release",
            rollup_name="Release", resource_group="Team", key_prefix="TEAM",
            total_story_points=1, completed_story_points=0, logged_hours=0,
            completed_logged_hours=0, story_point_ratio=0, percent_complete=0,
            in_planning=False, completed=False, target_start="2026-01-01",
            target_end="2026-02-01", successors=list(following),
        )
        audit.append(AuditItem(
            severity="Review", category="CascadeBranchDriver" if following else "CascadingDateChange",
            jira_key=key, schedule_key=key, field="Finish", summary=key + " summary",
            old_value="2026-01-01", new_value="2026-02-01",
        ))
    return RunPlan("2026-01-01", "synthetic.csv", "fixVersion", {}, {}, {}, epics, audit)


class ReportScalingTests(unittest.TestCase):
    def test_joined_dag_renders_each_issue_once_and_counts_unique_descendants(self):
        graph = {"TEAM-0": ["TEAM-1-A", "TEAM-1-B"]}
        for layer in range(1, 17):
            for side in ("A", "B"):
                graph[f"TEAM-{layer}-{side}"] = (
                    [f"TEAM-{layer+1}-A", f"TEAM-{layer+1}-B"] if layer < 16 else []
                )
        analyzed = CascadeGraph(graph)
        self.assertEqual(analyzed.downstream_counts["TEAM-0"], 32)
        html = render_schedule_cascade_review(graph_plan(graph), True)
        self.assertEqual(html.count('class="cascade-node '), 33)
        self.assertIn('class="cascade-reference"', html)
        self.assertIn("32 affected tasks", html)
        self.assertLess(len(html), 100000)

    def test_long_chain_is_iterative_and_keeps_complete_audit_csv(self):
        count = 1500
        graph = {f"TEAM-{n}": [f"TEAM-{n+1}"] if n < count-1 else [] for n in range(count)}
        plan = graph_plan(graph)
        html = render_schedule_cascade_review(plan, True)
        self.assertEqual(html.count('class="cascade-node '), CASCADE_MAX_DEPTH)
        self.assertIn("Visual tree shortened", html)
        self.assertIn(f"{count - 1} affected tasks", html)
        self.assertNotIn("TEAM-1499", html)  # Complete evidence remains in the CSV below.
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(plan, Path(tmp), deepcopy(DEFAULT_CONFIG))
            with paths["audit_detail"].open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), count)
            self.assertEqual(rows[-1]["jira_key"], "TEAM-1499")

    def test_budget_is_shared_across_branches(self):
        graph = {}
        for n in range(600):
            graph[f"TEAM-{n}-ROOT"] = [f"TEAM-{n}-LEAF"]
            graph[f"TEAM-{n}-LEAF"] = []
        html = render_schedule_cascade_review(graph_plan(graph), True)
        self.assertEqual(html.count('class="cascade-node '), CASCADE_MAX_ENTRIES)
        self.assertIn("Visual tree shortened", html)
        self.assertIn("audit-detail.csv", html)

    def test_one_wide_branch_closes_its_html_when_budget_runs_out(self):
        graph = {"TEAM-ROOT": [f"TEAM-{n}" for n in range(800)]}
        graph.update({f"TEAM-{n}": [] for n in range(800)})
        html = render_schedule_cascade_review(graph_plan(graph), True)
        self.assertEqual(html.count('class="cascade-node '), CASCADE_MAX_ENTRIES)
        self.assertEqual(html.count("<div"), html.count("</div>"))
        self.assertIn("800 affected tasks", html)

    def test_cycle_analysis_is_safe_for_direct_report_callers(self):
        graph = CascadeGraph({"A": ["B"], "B": ["A", "C"], "C": []})
        self.assertEqual(graph.downstream_counts, {"A": 2, "B": 2, "C": 0})

    def test_manifest_link_stays_in_index_without_cluttering_manager_reports(self):
        plan = graph_plan({"TEAM-1": ["TEAM-2"], "TEAM-2": []})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = write_reports(plan, root, deepcopy(DEFAULT_CONFIG))
            self.assertNotIn("run-manifest.json", paths["manager_report"].read_text())
            self.assertNotIn("run-manifest.json", paths["html_report_index"].read_text())
            (root / "run-manifest.json").write_text("{}")
            paths = write_reports(plan, root, deepcopy(DEFAULT_CONFIG))
            self.assertIn('href="../../run-manifest.json"', paths["html_report_index"].read_text())
            self.assertEqual((root / "run-manifest.json").read_text(), "{}")
            self.assertNotIn("run-manifest.json", paths["manager_report"].read_text())
            group = next(paths["resource_group_reports"].glob("*.html"))
            self.assertNotIn("run-manifest.json", group.read_text())
