"""All real yerp exports through selective writes with a portable Project fake.

These tests cover source planning and COM-boundary logic, not live scheduling,
Project edition behavior, or persistence of a real .mpp file.
"""
import csv
import hashlib
import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import ProjectAutomationError
from j2p.reports import write_reports
from j2p.run_lifecycle import RunTransaction
from yerp_project_support import FILES, YERP, changed_child_plan, project_from_yerp_plan


@unittest.skipUnless(bool(FILES) and (YERP / 'ssn-812-config.yaml').exists(),
                     'Requires the nine project-specific yerp exports and configuration')
class YerpProjectUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if len(FILES) != 9:
            raise AssertionError('Expected the nine September 18 yerp exports; refresh the dataset expectations if inputs change')
        cls.hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        cls.config = load_config(YERP / 'ssn-812-config.yaml')
        cls.original = build_run_plan(FILES, cls.config)

    @classmethod
    def tearDownClass(cls):
        if cls.hashes != {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}:
            raise AssertionError('A yerp source CSV changed during testing')

    def setUp(self):
        self.session, self.epics, self.summaries = project_from_yerp_plan(self.original, self.config)

    def apply(self, plan):
        with redirect_stdout(io.StringIO()):
            self.session.configure_custom_fields(self.config)
            self.session.apply_plan(plan, self.config)
            self.session.end_selective_update(plan)
            self.session.recalculate()
            verified = self.session.verify_plan(plan, self.config)
        return verified

    def test_full_real_plan_noop_has_zero_task_writes_and_complete_readback(self):
        self.session.begin_selective_update()
        before = self.session.snapshot_tasks(self.config)
        plan = build_run_plan(FILES, self.config, before)
        original_audit = [asdict(item) for item in plan.audit_items]
        verified = self.apply(plan)
        self.assertEqual((len(self.epics), len(self.summaries)), (2016, 164))
        self.assertEqual(sum(len(e.predecessors) for e in plan.epics.values()), 87)
        self.assertEqual([write for task in self.session.project.Tasks.items for write in task.writes], [])
        self.assertEqual(plan.stats['project_update_writes']['task_fields']['written'], 0)
        self.assertEqual(plan.stats['project_row_calculation_checkpoints'], [504, 1008, 1512, 2016])
        self.assertEqual(plan.stats['project_update_writes']['resource_fields']['written'], 0)
        self.assertEqual(plan.stats['project_update_writes']['dependency_sets']['written'], 0)
        self.assertGreater(verified, 50000)
        self.assertEqual([asdict(item) for item in plan.audit_items], original_audit)
        with redirect_stdout(io.StringIO()):
            self.session.add_schedule_review_items(plan, before, self.config)
        self.assertEqual([asdict(item) for item in plan.audit_items], original_audit)
        # Full verification must remain live even for previously skipped rows.
        self.epics['SSWCYBER-3219'].task.Number7 = 0
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'Number7'):
            self.session.verify_plan(plan, self.config)

    def test_actual_multiversion_child_change_updates_only_six_rows_and_six_rollups(self):
        self.session.begin_selective_update()
        before = self.session.snapshot_tasks(self.config)
        plan = changed_child_plan(self.config, before)
        original_audit = [asdict(item) for item in plan.audit_items]
        expected_epics = {key for key, epic in plan.epics.items() if asdict(epic) != asdict(self.original.epics[key])}
        expected_summaries = {key for key, summary in plan.summaries.items()
                              if asdict(summary) != asdict(self.original.summaries[key])}
        self.assertEqual((len(expected_epics), len(expected_summaries)), (6, 6))
        self.apply(plan)
        self.assertEqual({key for key, task in self.epics.items() if task.writes}, expected_epics)
        self.assertEqual({key for key, task in self.summaries.items() if task.writes}, expected_summaries)
        self.assertTrue(all(field.startswith('Number') or field == 'PercentComplete'
                            for task in self.session.project.Tasks.items for field, _ in task.writes))
        self.assertEqual([asdict(item) for item in plan.audit_items], original_audit)
        original_stats = deepcopy(plan.stats)
        with tempfile.TemporaryDirectory(prefix='j2p-yerp-report-test-') as tmp:
            transaction = RunTransaction({'run_dir': Path(tmp)}, None)
            transaction.manifest = {'timings_seconds': {}}
            transaction.record_plan(plan)
            paths = write_reports(plan, Path(tmp), self.config)
            with paths['planned_epics'].open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2016)
            self.assertEqual({row['schedule_key'] for row in rows}, set(plan.epics))
            with paths['summary_rollups'].open(newline='') as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 164)
            with paths['audit_detail'].open(newline='') as handle:
                report_audit = list(csv.DictReader(handle))
            self.assertEqual(len(report_audit), len(plan.audit_items))
            self.assertEqual(Counter(row['category'] for row in report_audit), Counter(a.category for a in plan.audit_items))
            groups = list(paths['resource_group_reports'].glob('*.html'))
            self.assertTrue(groups)
            for report in [paths['manager_report'], *groups]:
                html = report.read_text()
                self.assertNotIn('Project Update Operations', html)
                self.assertNotIn('Project Row Timing', html)
                self.assertNotIn('Report Context', html)
                self.assertIn('audit-detail.csv', html)
            recorded_stats = json.loads(transaction.manifest_path.read_text())['stats']
            for metric in ('project_update_writes', 'project_row_seconds', 'project_row_calculation_checkpoints'):
                self.assertEqual(recorded_stats[metric], original_stats[metric])
            self.assertIn('href="../../run-manifest.json"', paths['html_report_index'].read_text())
        self.assertEqual(plan.stats, original_stats)

    def test_actual_cyber_epic_keeps_six_percent_custom_value_when_native_recalculates(self):
        task = self.epics['SSWCYBER-3219']
        self.assertEqual(task.Number7, 6)
        task.task.PercentComplete = 0
        self.session.begin_selective_update()
        before = self.session.snapshot_tasks(self.config)
        plan = build_run_plan(FILES, self.config, before)
        self.apply(plan)
        self.assertEqual(task.writes, [])
        self.assertEqual((task.Number7, task.PercentComplete), (6, 0))
        self.assertTrue(any(item.category == 'ProjectNativeCompletionRecalculated'
                            and item.schedule_key == 'SSWCYBER-3219' for item in plan.audit_items))
        # The real export includes completed references too: they stay active without assignments,
        # and only driving completed rows carry native100%.
        completed_refs = [epic for epic in plan.epics.values() if epic.completed and not epic.drives_schedule]
        self.assertGreater(len(completed_refs), 0)
        for epic in completed_refs:
            self.assertTrue(self.epics[epic.key].Active)
            self.assertEqual(self.epics[epic.key].PercentComplete, 0)

    def test_schedule_changes_on_unwritten_real_dependency_rows_are_still_reported(self):
        self.session.begin_selective_update()
        before = self.session.snapshot_tasks(self.config)
        plan = build_run_plan(FILES, self.config, before)
        self.apply(plan)
        driver = next(epic for epic in plan.epics.values() if epic.drives_schedule and epic.successors)
        successor = driver.successors[0]
        for key in (driver.key, successor):
            self.epics[key].task.Finish = '2030-01-01'  # Explicit simulated scheduler effect.
            self.assertEqual(self.epics[key].writes, [])
        with redirect_stdout(io.StringIO()):
            self.session.add_schedule_review_items(plan, before, self.config)
        categories = {(item.schedule_key, item.category) for item in plan.audit_items}
        self.assertIn((driver.key, 'CascadeBranchDriver'), categories)
        self.assertIn((successor, 'CascadingDateChange'), categories)


if __name__ == '__main__':
    unittest.main()
