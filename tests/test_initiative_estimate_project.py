"""Estimate leaves survive selective writes, native copy syncing and retirement."""

import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace

from j2p.baseline import compare_with_baseline
from j2p.models import RunPlan
from j2p.project import ProjectAutomationError, summary_metric_fields
from j2p.reference_dates import synchronize_reference_dates
from j2p.rollups import build_summaries, summary_identity
from test_initiative_estimates import configuration, estimates, source
from test_project_update_integration import RecordingTask
from yerp_project_support import project_from_yerp_plan


def estimate_plan(config, points=21, child_points=0):
    initiative = source('TEAM-100', original_story_points=points,
                        fix_versions=['Shared', 'Next'])
    children = ([source('TEAM-1', 'Epic', parent=initiative.key,
                        original_story_points=child_points)] if child_points else [])
    rows, audit = estimates(initiative, children, config=config)
    return RunPlan('2026-10-08', 'in-memory.csv', 'fixVersion', {}, {},
                   build_summaries(rows, config), rows, audit)


class InitiativeEstimateProjectTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.plan = estimate_plan(self.config)
        self.session, self.rows, self.headers = project_from_yerp_plan(self.plan, self.config)

    def update(self, plan):
        with redirect_stdout(io.StringIO()):
            self.session.begin_selective_update()
            before = self.session.snapshot_tasks(self.config)
            self.session.configure_custom_fields(self.config)
            self.session.apply_plan(plan, self.config)
            self.session.end_selective_update(plan)
            self.session.recalculate()
            verified = self.session.verify_plan(plan, self.config)
        return before, verified

    def test_identical_estimate_update_does_not_write_and_verifies_real_issue_type(self):
        tasks = self.session.project.Tasks
        before, verified = self.update(self.plan)
        self.assertGreater(verified, 60)
        self.assertEqual(len(before), 4)
        self.assertTrue(all(before[key].issue_type == 'Initiative' for key in self.plan.epics))
        self.assertTrue(all(not task.writes for task in tasks.items))
        self.assertEqual(self.plan.stats['project_update_writes']['task_fields']['written'], 0)
        # Baseline, dependencies, and verification are the three existing scans;
        # estimate handling must not add a fourth traversal.
        self.assertEqual(tasks.count_reads, 3)
        self.assertEqual(tasks.item_reads, 3 * len(tasks.items))
        indexed = self.session.index_rollup_summaries(self.config, tasks.items)
        self.assertEqual(set(indexed), {('fixVersion', 'Shared'), ('fixVersion', 'Next')})
        self.assertIs(self.session.find_rollup_summary('fixVersion', 'Shared', self.config),
                      self.headers['fixVersion:Shared'])
        self.rows['TEAM-100::ESTIMATE'].task.Text3 = 'Epic'
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'Text3'):
            self.session.verify_plan(self.plan, self.config)

    def test_changed_original_estimate_only_writes_changed_metrics_and_preserves_diff(self):
        changed = estimate_plan(self.config, points=34)
        before, _ = self.update(changed)
        compare_with_baseline(changed.epics, changed.summaries, before, self.config, changed.audit_items)
        self.assertEqual({key for key, task in self.rows.items() if task.writes}, set(changed.epics))
        self.assertEqual({key for key, task in self.headers.items() if task.writes}, set(changed.summaries))
        self.assertTrue(all(field in {'Number1', 'Number5'}
                            for task in self.session.project.Tasks.items for field, _ in task.writes))
        diffs = [item for item in changed.audit_items if item.field == 'Total Story Points'
                 and item.category == 'ChangedField']
        self.assertEqual({item.schedule_key for item in diffs}, set(changed.epics))
        self.assertTrue(all((item.old_value, item.new_value) == ('21', '34') for item in diffs))
        self.assertTrue(all(item.issue_type == 'Initiative' for item in diffs))

    def test_initiative_layout_keeps_estimate_leaf_distinct_from_same_jira_key_header(self):
        self.config = configuration(rollup_modes={'TEAM': 'initiative'})
        self.plan = estimate_plan(self.config)
        self.session, self.rows, self.headers = project_from_yerp_plan(self.plan, self.config)
        before, _ = self.update(self.plan)
        self.assertEqual(set(before), {'TEAM-100', 'TEAM-100::ESTIMATE'})
        self.assertTrue(before['TEAM-100'].is_summary)
        self.assertFalse(before['TEAM-100::ESTIMATE'].is_summary)
        self.assertTrue(all(not task.writes for task in self.session.project.Tasks.items))

    def test_estimate_references_sync_to_scheduled_primary_and_keep_full_readback(self):
        primary = self.rows['TEAM-100::ESTIMATE']
        primary.task.Start += timedelta(days=7)
        primary.task.Finish += timedelta(days=14)
        reference_key = next(key for key, row in self.plan.epics.items() if not row.drives_schedule)
        expected = (primary.Start, primary.Finish)
        headers = {summary_identity(summary.rollup_mode, summary.key): self.headers[summary.summary_id]
                   for summary in self.plan.summaries.values()}
        with redirect_stdout(io.StringIO()):
            synchronize_reference_dates(self.session, self.plan, self.config, self.rows,
                                        headers, {'TEAM-100::ESTIMATE': expected})
            self.session.recalculate()
            self.session.verify_plan(self.plan, self.config)
        reference = self.rows[reference_key]
        self.assertEqual((primary.Start, primary.Finish), expected)
        self.assertEqual((reference.Start, reference.Finish), expected)
        self.assertTrue(reference.Active)
        self.assertTrue(reference.Manual)
        self.assertEqual(reference.Assignments.Count, 0)
        reference.task.Finish += timedelta(days=1)
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'reference dates'):
            self.session.verify_plan(self.plan, self.config)

    def test_retirement_clears_empty_headers_and_reactivation_restores_same_rows(self):
        consumed = estimate_plan(self.config, child_points=21)
        self.assertFalse(consumed.epics)
        original_ids = {key: task.UniqueID for key, task in self.rows.items()}
        self.update(consumed)
        for task in self.rows.values():
            self.assertFalse(task.Active)
            self.assertTrue(task.Flag2)
        for task in self.headers.values():
            self.assertTrue(all(getattr(task, field) == 0 for field in summary_metric_fields(self.config)))
        header = self.headers['fixVersion:Next']
        header.task.Number5 = 21
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'retired estimate rollup'):
            self.session.verify_plan(consumed, self.config)
        header.task.Number5 = 0
        primary = self.rows['TEAM-100::ESTIMATE']
        primary.task.Active = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'retired estimate='):
            self.session.verify_plan(consumed, self.config)
        primary.task.Active = False
        reference = next(task for key, task in self.rows.items() if key != 'TEAM-100::ESTIMATE')
        reference.task.Active = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'retired reference='):
            self.session.verify_plan(consumed, self.config)
        reference.task.Active = False
        restored = estimate_plan(self.config, points=34, child_points=8)
        self.update(restored)
        self.assertEqual({key: task.UniqueID for key, task in self.rows.items()}, original_ids)
        self.assertEqual(len(self.session.project.Tasks.items), 4)
        self.assertTrue(all(task.Active and not task.Flag2 and task.Number1 == 26
                            for task in self.rows.values()))
        self.assertTrue(all(task.Number5 == 26 for task in self.headers.values()))

    def test_retirement_preserves_header_with_unmanaged_active_descendant(self):
        shared = self.headers['fixVersion:Shared']
        human_parent = SimpleNamespace(ID=50, UniqueID=50000, Summary=True, Active=False,
                                       OutlineParent=shared)
        human = SimpleNamespace(ID=51, UniqueID=50001, Summary=False, Active=True,
                                OutlineParent=human_parent)
        self.session.project.Tasks.items.extend([human_parent, human])
        consumed = estimate_plan(self.config, child_points=21)
        self.update(consumed)
        self.assertEqual(shared.Number1, 21)
        self.assertEqual(shared.Number5, 21)
        self.assertEqual(self.headers['fixVersion:Next'].Number5, 0)
        self.assertTrue(human.Active)

    def test_retirement_leaves_unmatched_ordinary_task_and_its_header_unchanged(self):
        shared = self.headers['fixVersion:Shared']
        primary = self.rows['TEAM-100::ESTIMATE']
        values = vars(primary.task).copy()
        values.update(ID=50, UniqueID=50000, Text1='TEAM-999', Text10='TEAM-999',
                      Text11='Primary', Text13='TEAM-999', OutlineParent=shared, Number1=8)
        legacy = RecordingTask(SimpleNamespace(**values))
        self.session.project.Tasks.items.append(legacy)
        self.update(estimate_plan(self.config, child_points=21))
        self.assertTrue(legacy.Active)
        self.assertTrue(legacy.Flag2)
        self.assertEqual(legacy.Number1, 8)
        self.assertEqual(shared.Number1, 21)
        self.assertEqual(self.headers['fixVersion:Next'].Number5, 0)


if __name__ == '__main__':
    unittest.main()
