"""Manual-copy date properties, exact native readback, and failure diagnostics."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from unittest.mock import patch

from j2p.project import ProjectAutomationError
from j2p.reference_dates import verify_reference_dates
from test_reference_dates import DateTask, fixture, raw_dates, sync


class PlaceholderCopy(DateTask):
    """Manual blank dates can coexist with usable underlying date values.

    Model a finish write moving the underlying start while the manual start
    remains blank, as in the reported immediate SSWSW-10467 readback.
    """

    def __init__(self, start, finish):
        super().__init__(start, finish)
        self.Manual = True
        self.IsStartValid = self.IsFinishValid = False
        self.Duration = 480
        self.DurationText = ''

    @property
    def StartText(self):
        return super().StartText if self.IsStartValid else ''

    @StartText.setter
    def StartText(self, value):
        DateTask.StartText.fset(self, value)
        if self.ignore_field != 'StartText':
            self.IsStartValid = True

    @property
    def FinishText(self):
        return super().FinishText if self.IsFinishValid else ''

    @FinishText.setter
    def FinishText(self, value):
        DateTask.FinishText.fset(self, value)
        if self.ignore_field != 'FinishText':
            self.IsFinishValid = True
            if not self.IsStartValid:
                self._start = self._finish - timedelta(minutes=self.Duration)


class ManualCopyDateTests(unittest.TestCase):
    def test_blank_start_matching_underlying_date_is_written_before_finish(self):
        session, plan, config, tasks, summaries = fixture()
        primary = tasks['P1']
        tasks['R1'] = copy = PlaceholderCopy(primary.Start, primary.Start + timedelta(hours=8))
        primary_dates = raw_dates(plan, tasks)
        sync(session, plan, config, tasks, summaries)
        self.assertEqual([field for field, _ in copy.writes], ['StartText', 'FinishText'])
        self.assertTrue(copy.IsStartValid)
        self.assertTrue(copy.IsFinishValid)
        self.assertEqual((copy.Start, copy.Finish), (primary.Start, primary.Finish))
        self.assertEqual(raw_dates(plan, tasks), primary_dates)
        self.assertFalse(any(task.writes for task in summaries.values()))

    def test_matching_underlying_dates_do_not_hide_blank_manual_endpoints(self):
        for blank in ('Start', 'Finish', 'both'):
            with self.subTest(blank=blank):
                session, plan, config, tasks, summaries = fixture()
                primary = tasks['P1']
                tasks['R1'] = copy = PlaceholderCopy(primary.Start, primary.Finish)
                copy.IsStartValid = blank == 'Finish'
                copy.IsFinishValid = blank == 'Start'
                sync(session, plan, config, tasks, summaries)
                self.assertTrue(copy.IsStartValid)
                self.assertTrue(copy.IsFinishValid)
                self.assertEqual((copy.Start, copy.Finish), (primary.Start, primary.Finish))
                copy.writes.clear()
                sync(session, plan, config, tasks, summaries)
                self.assertEqual(copy.writes, [])

    def test_verification_rejects_blank_manual_dates_even_when_native_dates_match(self):
        for field in ('Start', 'Finish'):
            with self.subTest(field=field):
                session, plan, config, tasks, summaries = fixture()
                sync(session, plan, config, tasks, summaries)
                for task in tasks.values():
                    task.writes.clear()
                setattr(tasks['R1'], f'Is{field}Valid', False)
                with redirect_stdout(io.StringIO()), self.assertRaises(ProjectAutomationError) as error:
                    verify_reference_dates(session, plan, config, tasks, summaries,
                                           verification_stage='after reopen')
                self.assertIn(f'field={field}', str(error.exception))
                self.assertIn(f'Is{field}Valid=False', str(error.exception))
                self.assertIn('stage=after reopen', str(error.exception))
                self.assertFalse(any(task.writes for task in tasks.values()))

    def test_ignored_blank_start_write_cannot_pass_with_matching_native_date(self):
        session, plan, config, tasks, summaries = fixture()
        primary = tasks['P1']
        tasks['R1'] = copy = PlaceholderCopy(primary.Start, primary.Finish)
        copy.IsFinishValid = True
        copy.ignore_field = 'StartText'
        with self.assertRaises(ProjectAutomationError) as error:
            sync(session, plan, config, tasks, summaries)
        self.assertIn('IsStartValid=False', str(error.exception))
        self.assertIn("StartText=''", str(error.exception))
        self.assertIn('stage=after manual copy date writes', str(error.exception))
        self.assertFalse(session._reference_dates_synchronized)

    def test_unreadable_validity_flag_does_not_fall_back_to_native_date(self):
        session, plan, config, tasks, summaries = fixture()
        del tasks['R1'].IsStartValid
        with self.assertRaisesRegex(ProjectAutomationError, 'field=IsStartValid'):
            sync(session, plan, config, tasks, summaries)
        self.assertFalse(any(task.writes for task in tasks.values()))
        self.assertFalse(session._reference_dates_synchronized)

    def test_sync_does_not_use_generic_native_date_setters_on_manual_copies(self):
        session, plan, config, tasks, summaries = fixture()
        primary_dates = raw_dates(plan, tasks)
        def no_native_write(task, value):
            raise AssertionError('Use manual date properties for copies')
        with patch.object(DateTask, 'Start', property(DateTask.Start.fget, no_native_write)), \
             patch.object(DateTask, 'Finish', property(DateTask.Finish.fget, no_native_write)):
            sync(session, plan, config, tasks, summaries)
            with redirect_stdout(io.StringIO()):
                verify_reference_dates(session, plan, config, tasks, summaries)
        self.assertEqual(raw_dates(plan, tasks), primary_dates)
        self.assertEqual((tasks['R1'].Start, tasks['R1'].Finish), primary_dates['P1'])
        self.assertFalse(any(tasks[key].writes for key in primary_dates))
        self.assertFalse(any(task.writes for task in summaries.values()))

    def test_manual_dates_use_project_locale_with_year_and_minute_and_cache_shared_sources(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        self.assertEqual(session.app.DateFormat.call_count, 4)  # Two primaries across three copies.
        self.assertTrue(all(call.args[1] == 2 for call in session.app.DateFormat.call_args_list))
        self.assertEqual(tasks['R1'].writes[0], ('StartText', 'September 01, 2026 09:15 AM'))
        self.assertEqual(tasks['R1'].writes[1], ('FinishText', 'September 03, 2026 03:45 PM'))
        session.app.DateFormat.reset_mock()
        sync(session, plan, config, tasks, summaries)
        session.app.DateFormat.assert_not_called()
        self.assertEqual(plan.stats['project_reference_dates']['written'], 0)

    def test_localized_date_text_is_passed_through_without_python_parsing(self):
        session, plan, config, tasks, summaries = fixture()
        dates_by_token = {}
        def localize(value, fmt):
            token = 'lokales Datum ' + value.isoformat()
            dates_by_token[token] = value
            return token
        def set_start(task, token):
            task._start = dates_by_token[token]
        def set_finish(task, token):
            task._finish = dates_by_token[token]
        session.app.DateFormat.side_effect = localize
        with patch.object(DateTask, 'StartText', property(DateTask.StartText.fget, set_start)), \
             patch.object(DateTask, 'FinishText', property(DateTask.FinishText.fget, set_finish)):
            sync(session, plan, config, tasks, summaries)
        self.assertEqual((tasks['R1'].Start, tasks['R1'].Finish), raw_dates(plan, tasks)['P1'])

    def test_failed_date_formatting_does_not_guess_local_date_syntax(self):
        for result in (None, '', False, RuntimeError('DateFormat unavailable')):
            session, plan, config, tasks, summaries = fixture()
            if isinstance(result, Exception):
                session.app.DateFormat.side_effect = result
            else:
                session.app.DateFormat.side_effect = None
                session.app.DateFormat.return_value = result
            with self.subTest(result=result), self.assertRaisesRegex(ProjectAutomationError, 'Cannot format manual copy date'):
                sync(session, plan, config, tasks, summaries)
            self.assertFalse(any(task.writes for task in [*tasks.values(), *summaries.values()]))
            self.assertFalse(session._reference_dates_synchronized)

    def test_ignored_manual_write_reports_expected_actual_times_stage_and_mode(self):
        session, plan, config, tasks, summaries = fixture()
        tasks['R1'].ignore_field = 'StartText'
        with self.assertRaises(ProjectAutomationError) as error:
            sync(session, plan, config, tasks, summaries)
        for detail in ('reference=R1', 'field=Start', 'expected=2026-09-01 09:15:00',
                       'actual=2026-01-01 08:00:00', 'stage=after manual copy date writes',
                       'StartText=', 'FinishText=', 'Manual=True', 'Active=True'):
            self.assertIn(detail, str(error.exception))
        self.assertFalse(session._reference_dates_synchronized)

    def test_reopen_mismatch_reports_stage_without_repair_or_format_calls(self):
        session, plan, config, tasks, summaries = fixture()
        sync(session, plan, config, tasks, summaries)
        tasks['R1']._start += timedelta(minutes=1)
        session.app.DateFormat.reset_mock()
        for task in tasks.values():
            task.writes.clear()
        with redirect_stdout(io.StringIO()), self.assertRaises(ProjectAutomationError) as error:
            verify_reference_dates(session, plan, config, tasks, summaries, verification_stage='after reopen')
        self.assertIn('stage=after reopen', str(error.exception))
        self.assertIn('actual=2026-09-01 09:16:00', str(error.exception))
        self.assertFalse(any(task.writes for task in tasks.values()))
        session.app.DateFormat.assert_not_called()


if __name__ == '__main__':
    unittest.main()
