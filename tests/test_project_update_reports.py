"""Update measurements retain their scope without reducing report detail."""

import csv
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from j2p.config import DEFAULT_CONFIG
from j2p.models import AuditItem, PlanEpic, RunPlan
from j2p.reports import render_report_context, write_reports
from j2p.run_lifecycle import RunTransaction


class ProjectUpdateReportTests(unittest.TestCase):
    def make_plan(self):
        epics = {}
        audit = []
        for n, group in enumerate(("Alpha", "Beta"), 1):
            key = f"TEAM-{n}"
            epics[key] = PlanEpic(
                key=key, jira_key=key, issue_id=str(n), summary=key,
                status="Open", rollup_mode="fixVersion", rollup_key="Release",
                rollup_name="Release", resource_group=group, key_prefix="TEAM",
                total_story_points=3, completed_story_points=1, logged_hours=0,
                completed_logged_hours=0, story_point_ratio=0, percent_complete=33,
                in_planning=False, completed=False, target_start="2026-01-01",
                target_end="2026-02-01",
            )
            audit.append(AuditItem(
                severity="Review", category="ChangedField", jira_key=key,
                schedule_key=key, field="Name", old_value="Old", new_value=key,
            ))
        return RunPlan("2026-01-01", "synthetic.csv", "fixVersion", {}, {}, {}, epics, audit)

    def test_update_metrics_remain_run_wide_and_complete_audit_is_retained(self):
        plan = self.make_plan()
        plan.stats.update({
            "project_update_writes": {
                "task_fields": {"written": 2, "skipped": 81, "failed": 0},
                "dependency_sets": {"written": 0, "skipped": 2, "failed": 0},
            },
            "project_update_seconds": {"apply_changes": 1.234, "review_index": 0.5,
                                       "review_duration": 0.1, "review_candidates": 0.1, "review_columns": 0.2,
                                       "format_review": 2.0, "total": 9.876},
        })
        original_stats = deepcopy(plan.stats)
        with tempfile.TemporaryDirectory() as tmp:
            transaction = RunTransaction({"run_dir": Path(tmp)}, None)
            transaction.manifest = {"timings_seconds": {}}
            transaction.record_plan(plan)
            paths = write_reports(plan, Path(tmp), deepcopy(DEFAULT_CONFIG))
            reports = [paths["manager_report"], *paths["resource_group_reports"].glob("*.html")]
            self.assertEqual(len(reports), 3)
            for path in reports:
                html = path.read_text()
                self.assertNotIn("Project Update Operations", html)
                self.assertNotIn("Project Update Timing", html)
                self.assertNotIn("Report Context", html)
                self.assertIn("audit-detail.csv", html)
            manifest = json.loads(transaction.manifest_path.read_text())
            self.assertEqual(manifest["stats"], original_stats)
            self.assertEqual(manifest["stats"]["project_update_writes"]["task_fields"]["skipped"], 81)
            self.assertEqual(manifest["stats"]["project_update_seconds"]["total"], 9.876)
            self.assertIn('href="../../run-manifest.json"', paths["html_report_index"].read_text())
            with paths["audit_detail"].open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["jira_key"] for row in rows], ["TEAM-1", "TEAM-2"])
            self.assertEqual([(row["field"], row["old_value"], row["new_value"]) for row in rows],
                             [(item.field, item.old_value, item.new_value) for item in plan.audit_items])
        self.assertEqual(plan.stats, original_stats)

    def test_report_without_project_measurements_does_not_imply_zero_cost(self):
        html = render_report_context(self.make_plan(), None, None, Path("reports"))
        self.assertNotIn("Project Update Operations", html)
        self.assertNotIn("Project Update Timing", html)
        self.assertIn("not created in validate mode", html)


    def test_creation_timings_distinguish_rows_resources_and_checkpoints(self):
        plan = self.make_plan()
        plan.stats.update({
            "project_run_mode": "create",
            "project_update_seconds": {"new": 0.1, "apply_changes": 24.0, "dependencies": 4.0, "total": 30.0},
            "project_row_seconds": {"placement": 4.0, "values_and_resources": 15.0,
                                   "resources_within_values": 3.0, "checkpoint_calculation": 5.0, "total": 24.0},
        })
        html = render_report_context(plan, Path("new.mpp"), None, Path("reports"))
        self.assertIn("Project Creation Timing (Entire Run)", html)
        self.assertIn("Project Row Timing (Entire Run)", html)
        self.assertIn("Write and verify predecessor links", html)
        self.assertIn("Quarter-point calculations", html)
        self.assertIn("Resource time is part of epic-value time; do not add it again", html)
        self.assertNotIn("Project Update Timing", html)
        self.assertIn("<td>24.000</td>", html)
