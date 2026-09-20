"""Describe observed one-day schedules without changing Project's schedule."""

import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import MicrosoftProjectSession, ProjectAutomationError
from j2p.project_values import epic_assignments, missing_target_date_note
from test_project_selective_updates import CountingTask
from test_project_verification_scaling import verification_fixture


ONE_DAY_NOTE = (
    'Missing Jira target start/end. Project currently schedules this task for one day; '
    'Jira supplied no dates. Confirm the duration.'
)
YERP = Path(__file__).resolve().parents[1] / 'yerp'


class UnreadableDurationTask(CountingTask):
    @property
    def Duration(self):
        raise RuntimeError('Duration could not be read')


class ProjectOneDayReviewTests(unittest.TestCase):
    def plan(self, start='', end='', blocker='', versions='A'):
        config = load_config(None, {
            'resource_groups': {'TEAM': 'Team'},
            'rollup_modes': {'TEAM': 'fixVersion'},
        })
        rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Fix versions',
             'Story Points', 'Status', 'Target start', 'Target end', 'Outward issue link (Blocks)'],
            ['TEAM-1', 'Epic', 'Epic', '', versions, '', 'Open', start, end, blocker],
            ['TEAM-2', 'Task', 'Work', 'TEAM-1', versions, '3', 'Open', '', '', ''],
        ]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan(Path('synthetic.csv'), config)
        return plan, config

    def session(self, hours=8):
        session = object.__new__(MicrosoftProjectSession)
        session.project = SimpleNamespace(HoursPerDay=hours)
        return session

    def task(self, epic, config, duration=480, task_class=CountingTask):
        task = task_class()
        for field, value in epic_assignments(epic, config):
            object.__setattr__(task, field, value)
        object.__setattr__(task, 'ResourceGroup', epic.resource_group)
        object.__setattr__(task, 'Manual', False)
        object.__setattr__(task, 'Active', epic.drives_schedule)
        object.__setattr__(task, 'HideBar', bool(epic.completed))
        object.__setattr__(task, 'Start', '2026-09-21T08:00:00')
        object.__setattr__(task, 'Finish', '2026-09-21T17:00:00')
        if task_class is not UnreadableDurationTask:
            object.__setattr__(task, 'Duration', duration)
        return task

    def annotate(self, session, plan, config, tasks):
        with redirect_stdout(io.StringIO()) as output:
            session.add_one_day_schedule_reviews(plan, config, tasks)
        return output.getvalue()

    def notes(self, plan):
        return [a for a in plan.audit_items if a.category == 'MissingJiraTargetDates']

    def test_one_day_uses_project_hours_per_day_without_changing_any_schedule_field(self):
        for hours in (6, 8, 10):
            with self.subTest(hours_per_day=hours):
                plan, config = self.plan()
                epic = plan.epics['TEAM-1']
                task = self.task(epic, config, duration=hours * 60)
                before = (task.Start, task.Finish, task.Duration)
                self.annotate(self.session(hours), plan, config, {'TEAM-1': task})
                self.assertEqual(epic.dependency_review, ONE_DAY_NOTE)
                self.assertEqual(task.Text8, ONE_DAY_NOTE)
                self.assertTrue(task.Flag3)
                self.assertEqual((task.Start, task.Finish, task.Duration), before)
                self.assertEqual((epic.target_start, epic.target_end), ('', ''))
                self.assertEqual(task.writes, [('Text8', ONE_DAY_NOTE)])
                self.assertEqual(len(self.notes(plan)), 1)
                note = self.notes(plan)[0]
                self.assertEqual((note.severity, note.field, note.message, note.new_value),
                                 ('Info', 'Dependency Review', ONE_DAY_NOTE, ONE_DAY_NOTE))

    def test_zero_multiple_and_invalid_durations_do_not_claim_one_day(self):
        for duration in (0, -480, 1, 479, 481, 960, None, True, '480', '1 day',
                         float('nan'), float('inf')):
            with self.subTest(duration=duration):
                plan, config = self.plan()
                epic = plan.epics['TEAM-1']
                expected = epic.dependency_review
                task = self.task(epic, config, duration)
                self.annotate(self.session(), plan, config, {'TEAM-1': task})
                self.assertEqual(epic.dependency_review, expected)
                self.assertNotIn('for one day', task.Text8)
                self.assertEqual(task.writes, [])

    def test_missing_and_unreadable_duration_preserve_generic_note(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                plan, config = self.plan()
                epic = plan.epics['TEAM-1']
                task = self.task(epic, config,
                                 task_class=UnreadableDurationTask if unreadable else CountingTask)
                if not unreadable:
                    del task.Duration
                output = self.annotate(self.session(), plan, config, {'TEAM-1': task})
                self.assertEqual(task.Text8, missing_target_date_note(epic))
                self.assertEqual(task.writes, [])
                self.assertIn('1 duration(s) unconfirmed', output)

    def test_invalid_or_missing_day_length_never_assumes_eight_hours(self):
        for hours in (None, 0, -8, True, '8', float('nan'), float('inf')):
            with self.subTest(hours_per_day=hours):
                plan, config = self.plan()
                epic = plan.epics['TEAM-1']
                task = self.task(epic, config)
                self.annotate(self.session(hours), plan, config, {'TEAM-1': task})
                self.assertEqual(task.Text8, missing_target_date_note(epic))
                self.assertEqual(task.writes, [])
        session = self.session()
        del session.project.HoursPerDay
        self.annotate(session, plan, config, {'TEAM-1': task})
        self.assertEqual(task.writes, [])

    def test_partial_dates_and_reference_rows_are_not_one_day_placeholders(self):
        for start, end in (('2026-10-01', ''), ('', '2026-10-30'),
                           ('2026-10-01', '2026-10-30'), ('', '')):
            with self.subTest(start=start, end=end):
                plan, config = self.plan(start, end, versions='A;B')
                tasks = {key: self.task(epic, config) for key, epic in plan.epics.items()}
                before = {key: epic.dependency_review for key, epic in plan.epics.items()}
                self.annotate(self.session(), plan, config, tasks)
                for key, epic in plan.epics.items():
                    if epic.drives_schedule and not start and not end:
                        self.assertEqual(epic.dependency_review, ONE_DAY_NOTE)
                    else:
                        self.assertEqual(epic.dependency_review, before[key])
                        self.assertEqual(tasks[key].writes, [])
                        self.assertNotIn('for one day', tasks[key].Text8)

    def test_long_warning_details_and_custom_mapping_are_preserved(self):
        blockers = [f'MISSING-{n}' for n in range(20)]
        plan, config = self.plan(blocker=','.join(blockers))
        config['project_fields']['dependency_review'] = 'Text30'
        config['project_fields']['dependency_review_needed'] = 'Flag20'
        epic = plan.epics['TEAM-1']
        old_remainder = epic.dependency_review[len(missing_target_date_note(epic)):]
        task = self.task(epic, config)
        object.__setattr__(task, 'Flag20', False)
        self.annotate(self.session(), plan, config, {'TEAM-1': task})
        self.assertEqual(epic.dependency_review, ONE_DAY_NOTE + old_remainder)
        self.assertTrue(task.Text30.startswith(ONE_DAY_NOTE))
        self.assertLessEqual(len(task.Text30), 255)
        self.assertIn('Full details:', task.Text30)
        self.assertTrue(task.Flag20)
        self.assertEqual({field for field, _ in task.writes}, {'Text30', 'Flag20'})
        for blocker in blockers:
            self.assertIn(f'Missing dependency target {blocker}.', epic.dependency_review)
        self.assertEqual(len(self.notes(plan)), 1)

    def test_repeated_postpass_does_not_duplicate_audits_or_rewrite_equal_values(self):
        plan, config = self.plan(blocker='MISSING-1')
        epic = plan.epics['TEAM-1']
        task = self.task(epic, config)
        session = self.session()
        self.annotate(session, plan, config, {'TEAM-1': task})
        notes = copy.deepcopy(plan.audit_items)
        text = epic.dependency_review
        task.writes.clear()
        self.annotate(session, plan, config, {'TEAM-1': task})
        self.assertEqual(plan.audit_items, notes)
        self.assertEqual(epic.dependency_review, text)
        self.assertEqual(text.count(ONE_DAY_NOTE), 1)
        self.assertEqual(task.writes, [])

    def test_postpass_refreshes_cached_review_values_before_deciding_to_write(self):
        plan, config = self.plan()
        epic = plan.epics['TEAM-1']
        task = self.task(epic, config)
        session = self.session()
        session.begin_selective_update()
        session.read_task_value(task, 'Text8')
        session.read_task_value(task, 'Flag3')
        # Model another post-schedule operation having already put the final text
        # into Project after selective update collected an earlier baseline.
        object.__setattr__(task, 'Text8', ONE_DAY_NOTE)
        self.annotate(session, plan, config, {'TEAM-1': task})
        self.assertEqual(task.writes, [])
        object.__setattr__(task, 'Text8', 'External change after the first pass')
        object.__setattr__(task, 'Flag3', False)
        self.annotate(session, plan, config, {'TEAM-1': task})
        self.assertEqual(dict(task.writes), {'Text8': ONE_DAY_NOTE, 'Flag3': True})

    def test_fresh_selective_plan_defers_generic_text_and_preserves_one_day_note(self):
        before, config = self.plan()
        session = self.session()
        task = self.task(before.epics['TEAM-1'], config)
        self.annotate(session, before, config, {'TEAM-1': task})
        fresh, _ = self.plan()
        task.writes.clear()
        session.begin_selective_update()
        with patch.object(session, 'set_native_resource_group', return_value=False):
            session.update_epic_task(task, fresh.epics['TEAM-1'], config, fresh,
                                     defer_undated_review=True)
        self.assertEqual(task.writes, [])
        self.annotate(session, fresh, config, {'TEAM-1': task})
        self.assertEqual(task.writes, [])
        self.assertEqual(fresh.epics['TEAM-1'].dependency_review, ONE_DAY_NOTE)

    def test_duration_change_replaces_stale_one_day_note_and_preserves_other_warnings(self):
        plan, config = self.plan(blocker='MISSING-1')
        epic = plan.epics['TEAM-1']
        task = self.task(epic, config)
        session = self.session()
        self.annotate(session, plan, config, {'TEAM-1': task})
        object.__setattr__(task, 'Duration', 960)
        task.writes.clear()
        self.annotate(session, plan, config, {'TEAM-1': task})
        self.assertTrue(task.Text8.startswith(missing_target_date_note(epic)))
        self.assertNotIn('for one day', task.Text8)
        self.assertIn('Missing dependency target MISSING-1.', task.Text8)
        self.assertEqual(len(self.notes(plan)), 1)
        self.assertEqual(self.notes(plan)[0].message, missing_target_date_note(epic))
        self.assertEqual([field for field, _ in task.writes], ['Text8'])

    def test_new_jira_dates_clear_one_day_note_even_when_deferral_is_enabled(self):
        before, config = self.plan()
        session = self.session()
        task = self.task(before.epics['TEAM-1'], config)
        self.annotate(session, before, config, {'TEAM-1': task})
        after, _ = self.plan('2026-10-01', '2026-10-30')
        task.writes.clear()
        session.begin_selective_update()
        with patch.object(session, 'set_native_resource_group', return_value=False):
            session.update_epic_task(task, after.epics['TEAM-1'], config, after,
                                     defer_undated_review=True)
        self.annotate(session, after, config, {'TEAM-1': task})
        self.assertEqual(task.Text8, '')
        self.assertFalse(task.Flag3)
        self.assertEqual(self.notes(after), [])
        self.assertEqual([value for field, value in task.writes if field == 'Text8'], [''])

    def test_updated_plan_passes_full_verification_and_detects_changed_review_text(self):
        session, plan, config, tasks, _ = verification_fixture(epic_count=1, summary_count=1)
        epic = plan.epics['TEAM-1']
        epic.target_start = epic.target_end = ''
        epic.dependency_review = missing_target_date_note(epic)
        tasks[0].Text8, tasks[0].Flag3 = epic.dependency_review, True
        tasks[0].Duration = 480
        session.project.HoursPerDay = 8
        self.annotate(session, plan, config, {'TEAM-1': tasks[0]})
        self.assertEqual(len(self.notes(plan)), 1)  # The fallback adds a missing planning audit.
        with redirect_stdout(io.StringIO()):
            self.assertGreater(session.verify_plan(plan, config), 20)
        tasks[0].Text8 = 'stale review'
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'field=Text8'):
            session.verify_plan(plan, config)

    def test_real_yerp_mixed_durations_annotate_only_eligible_rows_and_leave_csvs_unchanged(self):
        files = sorted(YERP.glob('*.csv'))
        config_path = YERP / 'ssn-812-config.yaml'
        if len(files) != 9 or not config_path.is_file():
            self.skipTest('The nine private yerp exports are not available')
        before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        config = load_config(config_path)
        plan = build_run_plan(files, config)
        session = self.session()
        originals = {key: epic.dependency_review for key, epic in plan.epics.items()}
        tasks = {key: self.task(epic, config, (480, 960, 0)[index % 3])
                 for index, (key, epic) in enumerate(plan.epics.items())}
        eligible = {key for key, epic in plan.epics.items()
                    if epic.drives_schedule and not epic.target_start and not epic.target_end
                    and tasks[key].Duration == 480}
        self.assertTrue(eligible)
        self.annotate(session, plan, config, tasks)
        annotated = {key for key, epic in plan.epics.items()
                     if epic.dependency_review.startswith(ONE_DAY_NOTE)}
        self.assertEqual(annotated, eligible)
        for key, epic in plan.epics.items():
            with self.subTest(schedule_key=key):
                self.assertEqual(epic.dependency_review,
                                 ONE_DAY_NOTE + originals[key][len(missing_target_date_note(epic)):]
                                 if key in eligible else originals[key])
                self.assertTrue(all(field in {'Text8', 'Flag3'} for field, _ in tasks[key].writes))
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}, before_hashes)


if __name__ == '__main__':
    unittest.main()
