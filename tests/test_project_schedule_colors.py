"""Schedule-derived date colors and bounded Project reads, without native COM."""

import copy
import hashlib
import io
import unittest
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import test_project_creation_order as creation
import test_project_formatting_scaling as formatting
from j2p.config import DEFAULT_CONFIG, load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem, ProjectTaskSnapshot
from j2p.project import MicrosoftProjectSession, apply_plan_to_sandbox
from yerp_project_support import FILES, YERP


SCHEDULE_CHANGES = {'ScheduledStartChange', 'CascadeBranchDriver', 'CascadingDateChange'}


def schedule_plan(count=3):
    plan, config = creation.creation_plan()
    source = next(epic for epic in plan.epics.values() if epic.drives_schedule)
    plan.epics = {}
    for index in range(1, count + 1):
        key = f'TEAM-{index}'
        plan.epics[key] = replace(source, key=key, jira_key=key, issue_id=str(index),
                                  target_start='2026-09-01', target_end='2026-09-04',
                                  predecessors=[], successors=[], dependency_review='')
    plan.audit_items = []
    return plan, config


def dates(key, start='2026-09-01', finish='2026-09-04'):
    return ProjectTaskSnapshot(key=key, start=start, finish=finish)


def review_session():
    session = object.__new__(MicrosoftProjectSession)
    session.snapshot_tasks = Mock(side_effect=AssertionError('Unexpected full Project snapshot'))
    return session


def review(session, plan, before, config, after):
    with redirect_stdout(io.StringIO()):
        session.add_schedule_review_items(plan, before, config, after=after)


class CountedDateTask(SimpleNamespace):
    def __getattribute__(self, field):
        if field in {'Start', 'Finish'}:
            object.__getattribute__(self, 'date_reads').append(field)
        return super().__getattribute__(field)


def date_changes(plan):
    return [item for item in plan.audit_items if item.category in SCHEDULE_CHANGES]


def prepare_display_fixture(session, plan, tasks):
    session.app.Calculation = -1
    session.app.GanttBarStyleEdit = Mock(return_value=True)
    for task in tasks:
        task.Date3 = task.Date4 = None
    session._reference_summary_tasks = (id(plan), {
        (summary.rollup_mode, summary.key.upper()): SimpleNamespace(Manual=False, Date3=None, Date4=None)
        for summary in plan.summaries.values()
    })


class ProjectScheduleColorTests(unittest.TestCase):
    def test_start_only_shift_is_green_and_retains_jira_mismatch_evidence(self):
        plan, config = schedule_plan(1)
        before = {'TEAM-1': dates('TEAM-1')}
        after = {'TEAM-1': dates('TEAM-1', start='2026-09-02')}
        session = review_session()
        review(session, plan, before, config, after)
        self.assertEqual([(a.category, a.field, a.color, a.old_value, a.new_value)
                          for a in date_changes(plan)], [
            ('ScheduledStartChange', 'Start', 'changed_cell', '2026-09-01', '2026-09-02')])
        mismatches = [a for a in plan.audit_items if a.category == 'ScheduledDateMismatch']
        self.assertEqual([(a.field, a.color, a.old_value, a.new_value) for a in mismatches], [
            ('Start', 'review_needed', '2026-09-01', '2026-09-02')])
        session.snapshot_tasks.assert_not_called()
        self.assertEqual(plan.epics['TEAM-1'].target_start, '2026-09-01')

    def test_branch_drivers_and_downstream_finish_shifts_are_all_green(self):
        plan, config = schedule_plan(3)
        plan.epics['TEAM-1'].successors = ['TEAM-2']
        plan.epics['TEAM-2'].predecessors = ['TEAM-1']
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        changes = date_changes(plan)
        self.assertEqual({a.schedule_key: a.category for a in changes}, {
            'TEAM-1': 'CascadeBranchDriver', 'TEAM-2': 'CascadingDateChange',
            'TEAM-3': 'CascadingDateChange'})
        self.assertTrue(all(a.field == 'Finish' and a.color == 'changed_cell' for a in changes))
        self.assertFalse(any('red finish' in a.reviewer_action.lower() for a in changes))

    def test_unchanged_dates_and_changed_reference_rows_are_not_schedule_changes(self):
        plan, config = schedule_plan(2)
        plan.epics['TEAM-2'].drives_schedule = False
        plan.epics['TEAM-2'].primary_schedule_key = 'TEAM-1'
        before = {key: dates(key) for key in plan.epics}
        after = dict(before, **{'TEAM-2': dates('TEAM-2', '2030-01-01', '2030-01-05')})
        review(review_session(), plan, before, config, after)
        self.assertEqual(plan.audit_items, [])

    def test_existing_native_dates_are_baseline_even_if_jira_targets_differ(self):
        plan, config = schedule_plan(1)
        before = {'TEAM-1': dates('TEAM-1', '2026-09-08', '2026-09-11')}
        review(review_session(), plan, before, config, copy.deepcopy(before))
        self.assertEqual(date_changes(plan), [])
        self.assertEqual({a.field for a in plan.audit_items if a.category == 'ScheduledDateMismatch'},
                         {'Start', 'Finish'})

    def test_creation_compares_to_jira_targets_when_no_captured_baseline_exists(self):
        plan, config = schedule_plan(1)
        after = {'TEAM-1': dates('TEAM-1', '2026-09-08', '2026-09-11')}
        review(review_session(), plan, {}, config, after)
        self.assertEqual({(a.field, a.old_value, a.new_value, a.color) for a in date_changes(plan)}, {
            ('Start', '2026-09-01', '2026-09-08', 'changed_cell'),
            ('Finish', '2026-09-04', '2026-09-11', 'changed_cell')})

    def test_undated_creation_uses_captured_initial_schedule_for_both_dates(self):
        plan, config = schedule_plan(1)
        plan.epics['TEAM-1'].target_start = plan.epics['TEAM-1'].target_end = ''
        session = review_session()
        session._new_task_schedule_dates = {'TEAM-1': dates('TEAM-1', '2026-09-19', '2026-09-19')}
        review(session, plan, {}, config,
               {'TEAM-1': dates('TEAM-1', '2026-09-22', '2026-09-22')})
        self.assertEqual({(a.field, a.old_value, a.new_value) for a in date_changes(plan)}, {
            ('Start', '2026-09-19', '2026-09-22'), ('Finish', '2026-09-19', '2026-09-22')})
        self.assertFalse(any(a.category == 'ScheduledDateMismatch' for a in plan.audit_items))

    def test_original_three_argument_api_still_reads_after_snapshot(self):
        plan, config = schedule_plan(1)
        session = review_session()
        session.snapshot_tasks.side_effect = None
        session.snapshot_tasks.return_value = {'TEAM-1': dates('TEAM-1', finish='2026-09-07')}
        with redirect_stdout(io.StringIO()):
            session.add_schedule_review_items(plan, {'TEAM-1': dates('TEAM-1')}, config)
        session.snapshot_tasks.assert_called_once_with(config)
        self.assertEqual(len(date_changes(plan)), 1)

    def test_creation_captures_undated_baseline_before_first_calculation(self):
        plan, config = creation.creation_plan()
        undated = next(epic for epic in plan.epics.values() if epic.drives_schedule)
        undated.target_start = undated.target_end = ''
        session = creation.creation_session()
        original_update = session.update_epic_task.side_effect

        def update(task, epic, config, plan):
            original_update(task, epic, config, plan)
            task.Start = epic.target_start or '2026-09-19'
            task.Finish = epic.target_end or '2026-09-19'

        def recalculate():
            for row in session.project.Tasks.items:
                if getattr(row, 'Text10', '') == undated.key:
                    row.Start = row.Finish = '2026-10-01'

        session.update_epic_task.side_effect = update
        session.recalculate.side_effect = recalculate
        with redirect_stdout(io.StringIO()):
            session.apply_plan(plan, config, write_dependencies=False, append_only=True)
        baseline = session._new_task_schedule_dates[undated.key]
        self.assertEqual((baseline.start, baseline.finish), ('2026-09-19', '2026-09-19'))
        self.assertEqual(set(session._new_task_schedule_dates),
                         {key for key, epic in plan.epics.items() if epic.drives_schedule})
        review(session, plan, {}, config,
               {undated.key: dates(undated.key, '2026-10-01', '2026-10-01')})
        self.assertEqual({a.field for a in date_changes(plan) if a.schedule_key == undated.key},
                         {'Start', 'Finish'})

    def test_formatting_reuses_single_index_and_reads_only_two_native_date_fields(self):
        plan, config = schedule_plan(2)
        tasks = [CountedDateTask(**vars(formatting.task(key, index)), date_reads=[])
                 for index, key in enumerate(plan.epics, start=1)]
        for task in tasks:
            task.Start, task.Finish = '2026-09-08', '2026-09-11'
        session = formatting.formatting_session(tasks)
        prepare_display_fixture(session, plan, tasks)
        session.snapshot_tasks = Mock(side_effect=AssertionError('Unexpected full snapshot'))
        session.index_tasks_by_key = Mock(wraps=session.index_tasks_by_key)
        session.add_schedule_review_items = Mock(wraps=session.add_schedule_review_items)
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config,
                                           before={key: dates(key) for key in plan.epics})
        session.snapshot_tasks.assert_not_called()
        self.assertEqual(session.index_tasks_by_key.call_count, 1)
        self.assertEqual(session.project.collection.count_reads, 1)
        self.assertEqual(session.project.collection.item_reads, [1, 2])
        self.assertEqual(Counter(field for task in tasks for field in task.date_reads),
                         Counter({'Start': 2, 'Finish': 2}))
        self.assertEqual(len(date_changes(plan)), 4)
        self.assertEqual(session.add_schedule_review_items.call_count, 1)

    def test_create_mode_formatter_detects_shifts_without_explicit_before(self):
        plan, config = schedule_plan(1)
        plan.stats['project_run_mode'] = 'create'
        task = formatting.task('TEAM-1')
        task.Start, task.Finish = '2026-09-08', '2026-09-11'
        session = formatting.formatting_session([task])
        prepare_display_fixture(session, plan, [task])
        session.snapshot_tasks = Mock(side_effect=AssertionError('Unexpected full snapshot'))
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)
        self.assertEqual({a.field for a in date_changes(plan)}, {'Start', 'Finish'})
        session.snapshot_tasks.assert_not_called()

    def test_confirmed_schedule_green_survives_later_amber_for_same_cell(self):
        plan, config = schedule_plan(1)
        plan.audit_items = [
            AuditItem('Info', 'ScheduledStartChange', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field='Start', color='changed_cell',
                      old_value='2026-09-01', new_value='2026-09-08'),
            AuditItem('Review', 'CascadeBranchDriver', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field='Finish', color='changed_cell',
                      old_value='2026-09-04', new_value='2026-09-11'),
            AuditItem('Review', 'ScheduledDateMismatch', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field='Start', color='review_needed'),
            AuditItem('Review', 'ScheduledDateMismatch', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field='Finish', color='review_needed'),
            AuditItem('Review', 'ReviewCandidate', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field='Dependency Review', color='review_needed'),
        ]
        original = copy.deepcopy(plan.audit_items)
        session = formatting.formatting_session([formatting.task('TEAM-1')])
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)
        colors = {call.args[1]: call.args[2] for call in session.color_project_cell_error.call_args_list}
        self.assertEqual(colors['Date3'], config['colors']['changed_cell'])
        self.assertEqual(colors['Date4'], config['colors']['changed_cell'])
        self.assertEqual(colors['Text8'], config['colors']['review_needed'])
        self.assertEqual(plan.audit_items, original)

    def test_update_workflow_passes_baseline_to_formatter_without_separate_snapshot(self):
        plan, config = schedule_plan(1)
        before = {'TEAM-1': dates('TEAM-1')}
        session = MagicMock()
        session.snapshot_tasks.return_value = before
        session.__enter__.return_value = session
        with patch('j2p.project.MicrosoftProjectSession', return_value=session), redirect_stdout(io.StringIO()):
            apply_plan_to_sandbox(Path('sandbox.mpp'), plan, config)
        session.snapshot_tasks.assert_called_once_with(config)
        session.add_schedule_review_items.assert_not_called()
        session.apply_review_formatting.assert_called_once_with(plan, config, before=before)
        session.save.assert_called_once_with()
        session.verify_saved_plan.assert_called_once_with(plan, config)

    def test_explicit_native_and_display_columns_both_keep_schedule_change_colors(self):
        plan, config = schedule_plan(1)
        config['review_table']['exposed_columns'] = ['start', 'finish', 'schedule_start', 'schedule_finish']
        plan.audit_items = [
            AuditItem('Info', 'ScheduledStartChange', jira_key='TEAM-1', schedule_key='TEAM-1',
                      field=field, color='changed_cell', old_value='2026-09-01', new_value='2026-09-08')
            for field in ('Start', 'Finish')
        ]
        session = formatting.formatting_session([formatting.task('TEAM-1')])
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)
        colors = {call.args[1]: call.args[2] for call in session.color_project_cell_error.call_args_list}
        self.assertEqual(colors, {column: config['colors']['changed_cell']
                                  for column in ('Start', 'Finish', 'Date3', 'Date4')})

    @unittest.skipUnless(len(FILES) == 9 and (YERP / 'ssn-812-config.yaml').exists(),
                         'Requires the nine private yerp CSV exports')
    def test_real_yerp_date_changes_preserve_scope_and_reference_exclusion(self):
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        try:
            config = load_config(YERP / 'ssn-812-config.yaml')
            plan = build_run_plan(FILES, config)
            self.assertEqual((len(plan.epics), len(plan.summaries)), (2016, 164))
            original = {key: asdict(epic) for key, epic in plan.epics.items()}
            before = {key: dates(key, epic.target_start or '2026-09-19',
                                 epic.target_end or '2026-09-19') for key, epic in plan.epics.items()}
            after = {key: dates(key, '2035-01-01', '2035-01-02') for key in plan.epics}
            review(review_session(), plan, before, config, after)
            changes = date_changes(plan)
            primary_keys = {key for key, epic in plan.epics.items() if epic.drives_schedule}
            self.assertEqual(len(primary_keys), 1521)
            self.assertEqual({(a.schedule_key, a.field) for a in changes},
                             {(key, field) for key in primary_keys for field in ('Start', 'Finish')})
            self.assertTrue(all(a.color == 'changed_cell' for a in changes))
            self.assertTrue(any(a.category == 'CascadeBranchDriver' for a in changes))
            self.assertEqual({key: asdict(epic) for key, epic in plan.epics.items()}, original)
        finally:
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}, hashes)


if __name__ == '__main__':
    unittest.main()
