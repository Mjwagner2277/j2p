"""Reference display dates follow scheduled primaries without scheduling copies."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.config import DEFAULT_CONFIG
from j2p.models import PlanEpic, RunPlan
from j2p.project import ProjectAutomationError
from j2p.reference_dates import (
    reference_rollup_ids, synchronize_reference_dates, verify_reference_dates,
)
from j2p.rollups import build_summaries


def parsed(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    for pattern in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%m/%d/%Y %I:%M %p', '%B %d, %Y %I:%M %p'):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    raise ValueError(value)


class DateTask:
    """Model native summary read-only dates and Start's duration side effect."""

    def __init__(self, start, finish, *, summary=False, active=True):
        self._start, self._finish = start, finish
        self.Summary, self.Active, self.Manual = summary, active, False
        self.writes = []
        self.reads = {'Start': 0, 'Finish': 0}
        self.reject_field = None
        self.ignore_field = None

    @property
    def Start(self):
        self.reads['Start'] += 1
        return self._start

    @Start.setter
    def Start(self, value):
        if self.Summary:
            raise AssertionError('Native summary Start is read-only')
        self.assign('Start', value)

    @property
    def Finish(self):
        self.reads['Finish'] += 1
        return self._finish

    @Finish.setter
    def Finish(self, value):
        if self.Summary:
            raise AssertionError('Native summary Finish is read-only')
        self.assign('Finish', value)

    @property
    def StartText(self):
        return parsed(self._start).strftime('%B %d, %Y %I:%M %p')

    @StartText.setter
    def StartText(self, value):
        if not self.Manual:
            raise RuntimeError('StartText requires manual scheduling')
        self.assign('StartText', value)

    @property
    def FinishText(self):
        return parsed(self._finish).strftime('%B %d, %Y %I:%M %p')

    @FinishText.setter
    def FinishText(self, value):
        if not self.Manual:
            raise RuntimeError('FinishText requires manual scheduling')
        self.assign('FinishText', value)

    def assign(self, field, value):
        if field == self.reject_field:
            raise RuntimeError('Synthetic Project rejection')
        self.writes.append((field, value))
        if field == self.ignore_field:
            return
        value = parsed(value)
        if field.startswith('Start'):
            try:
                duration = parsed(self._finish) - parsed(self._start)
            except (TypeError, ValueError):
                duration = timedelta(hours=8)
            self._start, self._finish = value, value + duration
        else:
            self._finish = value


def fixture():
    config = copy.deepcopy(DEFAULT_CONFIG)
    epics = {}
    for key, release, primary in (
        ('P1', 'A', ''), ('P2', 'A', ''), ('P3', 'C', ''),
        ('R1', 'B', 'P1'), ('R2', 'B', 'P2'), ('R3', 'C', 'P1'),
    ):
        epics[key] = PlanEpic(
            key=key, jira_key=primary or key, issue_id=key, summary=key,
            status='Open', rollup_mode='fixVersion', rollup_key=release,
            rollup_name=release, resource_group='Team', key_prefix='TEAM',
            total_story_points=3, completed_story_points=1, logged_hours=0,
            completed_logged_hours=0, story_point_ratio=0, percent_complete=33,
            in_planning=False, completed=False, target_start='2026-01-01',
            target_end='2026-01-02', drives_schedule=not primary,
            primary_schedule_key=primary or key, row_role='Reference' if primary else 'Primary',
        )
    plan = RunPlan('2026-09-01', 'test.csv', 'fixVersion', {}, {},
                   build_summaries(epics, config), epics, [])
    tasks = {
        'P1': DateTask(datetime(2026, 9, 1, 9, 15), datetime(2026, 9, 3, 15, 45)),
        'P2': DateTask(datetime(2026, 8, 28, 6, 30), datetime(2026, 9, 5, 11, 20)),
        'P3': DateTask(datetime(2026, 8, 20, 10, 10), datetime(2026, 9, 2, 14, 35)),
    }
    for key in ('R1', 'R2', 'R3'):
        tasks[key] = DateTask(datetime(2026, 1, 1, 8), datetime(2026, 1, 2, 17), active=False)
    summaries = {('fixVersion', key): DateTask(datetime(2026, 1, 1, 8), datetime(2026, 1, 2, 17), summary=True)
                 for key in ('A', 'B', 'C')}
    session = SimpleNamespace(
        app=SimpleNamespace(DateFormat=Mock(side_effect=lambda value, fmt: parsed(value).strftime('%B %d, %Y %I:%M %p'))),
        read_task_value=Mock(side_effect=AssertionError('Must not use stale value cache')),
        write_task_value=Mock(side_effect=AssertionError('Must not use stale write cache')),
    )
    return session, plan, config, tasks, summaries


def raw_dates(plan, tasks):
    return {key: (tasks[key]._start, tasks[key]._finish)
            for key, epic in plan.epics.items() if epic.drives_schedule}


def sync(session, plan, config, tasks, summaries, raw=None):
    with redirect_stdout(io.StringIO()):
        synchronize_reference_dates(session, plan, config, tasks, summaries,
                                    raw_dates(plan, tasks) if raw is None else raw)


class ReferenceDateTests(unittest.TestCase):
    def test_reference_only_and_mixed_rollups_use_final_primary_windows(self):
        session, plan, config, tasks, summaries = fixture()
        before_epics, before_summaries = copy.deepcopy(plan.epics), copy.deepcopy(plan.summaries)
        sync(session, plan, config, tasks, summaries)
        for key, primary in (('R1', 'P1'), ('R2', 'P2'), ('R3', 'P1')):
            self.assertEqual((tasks[key].Start, tasks[key].Finish), (tasks[primary].Start, tasks[primary].Finish))
            self.assertFalse(tasks[key].Active)
            self.assertFalse(tasks[key].Manual)
        self.assertEqual((summaries['fixVersion', 'B'].Start, summaries['fixVersion', 'B'].Finish),
                         (tasks['P2'].Start, tasks['P2'].Finish))
        self.assertEqual((summaries['fixVersion', 'C'].Start, summaries['fixVersion', 'C'].Finish),
                         (tasks['P3'].Start, tasks['P1'].Finish))
        self.assertFalse(summaries['fixVersion', 'A'].Manual)
        self.assertEqual(summaries['fixVersion', 'A'].writes, [])
        self.assertTrue(all(not tasks[key].writes for key in ('P1', 'P2', 'P3')))
        self.assertEqual(plan.epics, before_epics)
        self.assertEqual(plan.summaries, before_summaries)
        self.assertEqual(plan.stats['project_reference_dates']['references'], 3)
        self.assertEqual(plan.stats['project_reference_dates']['rollups'], 2)
        self.assertTrue(session._reference_dates_synchronized)

    def test_manual_summary_dates_use_text_fields_and_project_localized_time_format(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        self.assertEqual({field for task in summaries.values() for field, _ in task.writes}, {'StartText', 'FinishText'})
        self.assertTrue(all(call.args[1] == 2 for call in session.app.DateFormat.call_args_list))
        self.assertEqual(summaries['fixVersion', 'C'].Finish, datetime(2026, 9, 3, 15, 45))

    def test_unchanged_rerun_writes_no_dates_and_does_not_consult_value_cache(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        for task in [*tasks.values(), *summaries.values()]:
            task.writes.clear()
        session.app.DateFormat.reset_mock()
        sync(session, plan, config, tasks, summaries)
        self.assertEqual(plan.stats['project_reference_dates']['written'], 0)
        self.assertEqual(plan.stats['project_reference_dates']['skipped'], 12)
        self.assertFalse(any(task.writes for task in [*tasks.values(), *summaries.values()]))
        session.app.DateFormat.assert_not_called()
        session.read_task_value.assert_not_called()

    def test_finish_is_rechecked_after_start_moves_it(self):
        session, plan, config, tasks, summaries = fixture()
        tasks['R1']._finish = tasks['P1']._finish
        sync(session, plan, config, tasks, summaries)
        self.assertEqual([field for field, _ in tasks['R1'].writes], ['Start', 'Finish'])
        self.assertEqual(tasks['R1'].Finish, tasks['P1'].Finish)

    def test_primary_change_updates_references_and_both_membership_rollups(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        tasks['P1']._finish = datetime(2026, 10, 9, 19, 5)
        for task in [*tasks.values(), *summaries.values()]:
            task.writes.clear()
        sync(session, plan, config, tasks, summaries)
        self.assertEqual({key for key, task in tasks.items() if task.writes}, {'R1', 'R3'})
        for release in ('B', 'C'):
            self.assertEqual(summaries['fixVersion', release].Finish, tasks['P1'].Finish)
        self.assertEqual(plan.stats['project_reference_dates']['written'], 4)

    def test_missing_primary_or_date_readback_fails_before_any_date_writes(self):
        for missing in ('plan', 'task', 'dates'):
            session, plan, config, tasks, summaries = fixture()
            dates = raw_dates(plan, tasks)
            if missing == 'plan':
                del plan.epics['P1']
            elif missing == 'task':
                del tasks['P1']
            else:
                del dates['P1']
            with self.subTest(missing=missing), self.assertRaises(ProjectAutomationError):
                sync(session, plan, config, tasks, summaries, dates)
            self.assertFalse(any(task.writes for task in [*tasks.values(), *summaries.values()]))
            self.assertFalse(session._reference_dates_synchronized)

    def test_invalid_or_inverted_primary_dates_fail_closed(self):
        for window in ((None, 'NA'), ('NA', '2026-09-02'), ('2026-09-03', '2026-09-01')):
            session, plan, config, tasks, summaries = fixture()
            dates = raw_dates(plan, tasks)
            dates['P1'] = window
            with self.subTest(window=window), self.assertRaisesRegex(ProjectAutomationError, 'primary=P1'):
                sync(session, plan, config, tasks, summaries, dates)
            self.assertFalse(any(task.writes for task in [*tasks.values(), *summaries.values()]))

    def test_supported_portable_date_values_preserve_endpoint_times(self):
        session, plan, config, tasks, summaries = fixture()
        tasks['P1']._start, tasks['P1']._finish = '09/01/2026 09:15 AM', '2026-09-03T15:45:00'
        tasks['P2']._start = date(2026, 8, 28)
        sync(session, plan, config, tasks, summaries)
        self.assertEqual(tasks['R1'].Start, datetime(2026, 9, 1, 9, 15))
        self.assertEqual(tasks['R1'].Finish, datetime(2026, 9, 3, 15, 45))
        self.assertEqual(summaries['fixVersion', 'B'].Start, datetime(2026, 8, 28))

    def test_reference_removal_changes_managed_rollup_eligibility(self):
        _, plan, _, _, _ = fixture()
        self.assertEqual(reference_rollup_ids(plan), {'fixVersion:B', 'fixVersion:C'})
        del plan.epics['R1'], plan.epics['R2']
        self.assertEqual(reference_rollup_ids(plan), {'fixVersion:C'})
        del plan.epics['R3']
        self.assertEqual(reference_rollup_ids(plan), set())

    def test_rejected_reference_and_summary_writes_fail_instead_of_reporting_success(self):
        for target, field in (('R1', 'Finish'), ('B', 'FinishText')):
            session, plan, config, tasks, summaries = fixture()
            task = tasks[target] if target in tasks else summaries['fixVersion', target]
            task.reject_field = field
            with self.subTest(target=target), self.assertRaisesRegex(ProjectAutomationError, 'rejected reference schedule write'):
                sync(session, plan, config, tasks, summaries)
            self.assertFalse(session._reference_dates_synchronized)

    def test_silent_write_failure_is_detected_by_native_pair_readback(self):
        for target, field in (('R1', 'Finish'), ('B', 'FinishText')):
            session, plan, config, tasks, summaries = fixture()
            task = tasks[target] if target in tasks else summaries['fixVersion', target]
            task.ignore_field = field
            with self.subTest(target=target), self.assertRaisesRegex(ProjectAutomationError, 'did not retain synchronized reference dates'):
                sync(session, plan, config, tasks, summaries)
            self.assertFalse(session._reference_dates_synchronized)

    def test_verification_uses_fresh_primary_dates_once_and_writes_nothing(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        for task in [*tasks.values(), *summaries.values()]:
            task.writes.clear()
            task.reads = {'Start': 0, 'Finish': 0}
        self.assertEqual(verify_reference_dates(session, plan, config, tasks, summaries), 18)
        for key in ('P1', 'P2', 'P3'):
            self.assertEqual(tasks[key].reads, {'Start': 1, 'Finish': 1})
        self.assertFalse(any(task.writes for task in [*tasks.values(), *summaries.values()]))
        tasks['P1']._finish += timedelta(days=1)
        with self.assertRaisesRegex(ProjectAutomationError, 'primary=P1'):
            verify_reference_dates(session, plan, config, tasks, summaries)

    def test_verification_rejects_primary_shift_inside_unchanged_mixed_rollup_span(self):
        session, plan, config, tasks, summaries = fixture()
        # P1 defines both outer boundaries. P3 has no reference rows, and moving
        # it within those boundaries leaves every reference and rollup correct.
        tasks['P3']._start = datetime(2026, 9, 2, 10, 10)
        tasks['P3']._finish = datetime(2026, 9, 2, 14, 35)
        summary = summaries['fixVersion', 'C']
        assign = summary.assign

        def move_child(field, value):
            assign(field, value)
            if field == 'StartText':
                tasks['P3']._start += timedelta(hours=1)
                tasks['P3']._finish += timedelta(hours=1)

        summary.assign = move_child
        sync(session, plan, config, tasks, summaries)
        self.assertEqual((summary.Start, summary.Finish), (tasks['P1'].Start, tasks['P1'].Finish))
        for task in [*tasks.values(), *summaries.values()]:
            task.reads = {'Start': 0, 'Finish': 0}
        with self.assertRaisesRegex(ProjectAutomationError, 'primary=P3'):
            verify_reference_dates(session, plan, config, tasks, summaries)
        for key in ('P1', 'P2', 'P3'):
            self.assertEqual(tasks[key].reads, {'Start': 1, 'Finish': 1})

    def test_verification_without_sync_history_still_checks_live_relationships(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        self.assertEqual(verify_reference_dates(SimpleNamespace(), plan, config, tasks, summaries), 18)
        tasks['R1']._finish += timedelta(minutes=1)
        with self.assertRaisesRegex(ProjectAutomationError, 'reference=R1'):
            verify_reference_dates(SimpleNamespace(), plan, config, tasks, summaries)

    def test_failed_sync_clears_previous_primary_snapshot_and_success_marker(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        self.assertIsNotNone(session._reference_primary_date_stamps)
        tasks['P1']._finish += timedelta(days=1)
        tasks['R1'].reject_field = 'Finish'
        with self.assertRaises(ProjectAutomationError):
            sync(session, plan, config, tasks, summaries)
        self.assertIsNone(session._reference_primary_date_stamps)
        self.assertFalse(session._reference_dates_synchronized)

    def test_verification_detects_reopened_modes_and_summary_date_corruption(self):
        for problem in ('active reference', 'manual reference', 'automatic summary', 'summary finish'):
            session, plan, config, tasks, summaries = fixture()
            sync(session, plan, config, tasks, summaries)
            if problem == 'active reference':
                tasks['R1'].Active = True
            elif problem == 'manual reference':
                tasks['R1'].Manual = True
            elif problem == 'automatic summary':
                summaries['fixVersion', 'B'].Manual = False
            else:
                summaries['fixVersion', 'B']._finish += timedelta(hours=1)
            with self.subTest(problem=problem), self.assertRaises(ProjectAutomationError):
                verify_reference_dates(session, plan, config, tasks, summaries)


if __name__ == '__main__':
    unittest.main()
