"""Active-copy migration, isolation, native rollups, and persistence boundaries."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.baseline import compare_with_baseline
from j2p.project import ProjectAutomationError
from j2p.reference_dates import synchronize_reference_dates
from j2p.rollups import build_summaries
from test_project_reliability import Assignments
from test_reference_pipeline import fixture
from test_reference_dates import fixture as date_fixture, raw_dates


class ActiveCopyTests(unittest.TestCase):
    def test_old_inactive_copy_loses_only_owned_assignment_before_activation(self):
        session, plan, config, tasks, summaries, epic = fixture()
        task = tasks[epic.key]
        task.task.Active = False
        task.task.Manual = False
        task.task.Assignments = Assignments()
        owned = session.project.Resources.items[0]
        task.Assignments.Add(owned.ID)
        task.task.Work = 2400
        old_targets = (task.Date1, task.Date2)
        old_native = (task.Start, task.Finish)
        primary_assignments = tasks[epic.primary_schedule_key].Assignments.Count
        with redirect_stdout(io.StringIO()):
            session.update_epic_task(task, epic, config, plan)
        self.assertTrue(task.Active)
        self.assertTrue(task.Manual)
        self.assertEqual(task.Assignments.Count, 0)
        self.assertEqual(task.Work, 0)
        self.assertEqual(tasks[epic.primary_schedule_key].Assignments.Count, primary_assignments)
        self.assertEqual((task.Date1, task.Date2), old_targets)
        self.assertEqual((task.Start, task.Finish), old_native)
        self.assertFalse(any(field in ('Start', 'Finish', 'PercentComplete') for field, _ in task.writes))

    def test_unmanaged_copy_resources_or_actuals_are_preserved_and_block_activation(self):
        for problem in ('unmanaged', 'actual_work', 'actual_duration', 'dependency'):
            session, plan, config, tasks, _, epic = fixture()
            task = tasks[epic.key]
            task.task.Active = False
            task.task.Assignments = Assignments()
            if problem == 'unmanaged':
                resource = SimpleNamespace(ID=500, Group='Human', Name='Person', Notes='Human resource')
                session.project.Resources.items.append(resource)
                task.Assignments.Add(resource.ID)
            elif problem == 'actual_work':
                task.task.ActualWork = 60
            elif problem == 'actual_duration':
                task.task.ActualDuration = 60
            else:
                task.TaskDependencies.items.append(object())
            before = (task.Assignments.Count, task.ActualWork, task.ActualDuration, task.TaskDependencies.Count)
            with self.subTest(problem=problem), self.assertRaises(ProjectAutomationError):
                session.update_epic_task(task, epic, config, plan)
            self.assertFalse(task.Active)
            self.assertEqual((task.Assignments.Count, task.ActualWork, task.ActualDuration, task.TaskDependencies.Count), before)

    def test_copy_is_retired_when_its_membership_disappears(self):
        session, plan, config, tasks, _, epic = fixture()
        task = tasks[epic.key]
        del plan.epics[epic.key]
        plan.summaries = build_summaries(plan.epics, config)
        session.mark_unmatched_tasks(plan, config, tasks)
        self.assertFalse(task.Active)
        self.assertTrue(task.Flag2)
        self.assertTrue(tasks[epic.primary_schedule_key].Active)

    def test_unchanged_copy_resource_group_is_not_a_false_change(self):
        session, plan, config, tasks, _, epic = fixture()
        baseline = session.snapshot_tasks(config)
        audit = []
        compare_with_baseline(plan.epics, plan.summaries, baseline, config, audit)
        self.assertFalse(any(a.schedule_key == epic.key and a.field == 'Resource Group' for a in audit))

    def test_duplicate_membership_or_wrong_primary_issue_fails_before_writes(self):
        for problem in ('duplicate', 'wrong issue'):
            session, plan, config, tasks, summaries = date_fixture()
            if problem == 'duplicate':
                plan.epics['R4'] = replace(plan.epics['R1'], key='R4')
                tasks['R4'] = tasks['R1']
            else:
                plan.epics['R1'].primary_schedule_key = 'P2'
            with self.subTest(problem=problem), self.assertRaises(ProjectAutomationError):
                synchronize_reference_dates(session, plan, config, tasks, summaries, raw_dates(plan, tasks))
            self.assertFalse(any(task.writes for task in tasks.values()))

    def test_primary_shift_during_copy_recalculation_is_rejected(self):
        session, plan, config, tasks, _, epic = fixture()
        primary = tasks[epic.primary_schedule_key]
        primary.task.Finish += timedelta(days=10)
        calculate_summaries = session.recalculate.side_effect
        def calculate():
            calculate_summaries()
            primary.task.Start += timedelta(minutes=1)
        session.recalculate.side_effect = calculate
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'stage=after copy calculation'):
            session.apply_review_formatting(plan, config, before={})
        self.assertEqual(primary.writes, [])

    def test_saved_plan_checks_copies_headers_and_primary_dates_before_and_after_reopen(self):
        for corruption in ('none', 'copy', 'header', 'primary', 'assignment', 'dependency', 'work'):
            session, plan, config, tasks, summaries, epic = fixture()
            with redirect_stdout(io.StringIO()):
                session.apply_review_formatting(plan, config, before={})
            project = session.project
            session.require_saved_file = Mock()
            session.close_project = Mock()
            def reopen(path):
                session.project = project
                if corruption == 'copy':
                    tasks[epic.key].task.Finish += timedelta(minutes=1)
                elif corruption == 'header':
                    summaries['fixVersion:Release B'].task._finish_native += timedelta(minutes=1)
                elif corruption == 'primary':
                    tasks[epic.primary_schedule_key].task.Start += timedelta(minutes=1)
                elif corruption == 'assignment':
                    tasks[epic.key].Assignments.items.append(object())
                elif corruption == 'work':
                    tasks[epic.key].task.Work = 480
                elif corruption == 'dependency':
                    tasks[epic.key].TaskDependencies.items.append(object())
            session.open = Mock(side_effect=reopen)
            with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                session.project_path = Path(tmp) / 'sandbox.mpp'
                session.project_path.write_bytes(b'Synthetic persistence; no native Project engine')
                if corruption == 'none':
                    session.verify_saved_plan(plan, config)
                    self.assertTrue(session.saved_successfully)
                    self.assertTrue(plan.stats['project_verification']['save_reopen'])
                else:
                    with self.subTest(corruption=corruption), self.assertRaises(ProjectAutomationError):
                        session.verify_saved_plan(plan, config)
                    self.assertFalse(session.saved_successfully)
                    self.assertNotIn('project_verification', plan.stats)
            session.close_project.assert_called_once_with(save_changes=False)
            session.open.assert_called_once()

    def test_creation_mode_syncs_final_primary_dates_and_native_summary(self):
        session, plan, config, tasks, summaries, epic = fixture()
        plan.stats['project_run_mode'] = 'create'
        primary = tasks[epic.primary_schedule_key]
        primary.task.Finish += timedelta(days=30)
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)
        self.assertEqual(tasks[epic.key].Finish, primary.Finish)
        self.assertEqual(summaries['fixVersion:Release B'].Finish, primary.Finish)
        self.assertEqual(primary.writes, [])
        session.recalculate.assert_called_once()

    def test_retired_copy_reactivation_cannot_hide_inside_a_summary_span(self):
        session, plan, config, tasks, _, epic = fixture()
        del plan.epics[epic.key]
        plan.summaries = build_summaries(plan.epics, config)
        session.mark_unmatched_tasks(plan, config, tasks)
        tasks[epic.key].task.Active = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'retired reference=.*Active'):
            session.verify_plan(plan, config)

    def test_field_mapping_document_omits_retired_custom_date_snapshots(self):
        from j2p.reports import write_field_mapping
        _, _, config, _, _, _ = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'FIELD_MAPPING.md'
            write_field_mapping(path, config)
            content = path.read_text()
        self.assertIn('Start / Finish', content)
        self.assertNotIn('`schedule_start`', content)
        self.assertNotIn('`schedule_finish`', content)

    def test_story_point_change_colors_the_visible_completion_field(self):
        from j2p.project import project_column_for_audit_field, review_table_columns
        _, _, config, _, _, _ = fixture()
        column = project_column_for_audit_field('% Complete', config)
        self.assertEqual(column, config['project_fields']['completion_percent'])
        self.assertIn(column, review_table_columns(config, []))

    def test_completed_points_count_in_each_release_but_only_once_overall(self):
        _, plan, config, _, _, epic = fixture()
        primary = plan.epics[epic.primary_schedule_key]
        for row in (primary, epic):
            row.total_story_points = row.completed_story_points = 3
            row.percent_complete = 100
        other = next(row for row in plan.epics.values() if row.key not in (primary.key, epic.key))
        other.total_story_points = other.completed_story_points = 0
        summaries = build_summaries(plan.epics, config)
        self.assertEqual(sum(summary.total_story_points for summary in summaries.values()), 3)
        self.assertEqual(sum(summary.completed_story_points for summary in summaries.values()), 3)
        for summary in summaries.values():
            self.assertEqual(summary.completion_total_story_points, 3)
            self.assertEqual(summary.completion_completed_story_points, 3)
            self.assertEqual(summary.percent_complete, 100)


if __name__ == '__main__':
    unittest.main()
